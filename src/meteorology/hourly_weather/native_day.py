"""Bounded, resumable, eight-core HRRR native UTC-day source pilot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import re
import ssl
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..era5.resources import (
    Budget,
    Limits,
    LimitExceeded,
    atomic_json,
    _bounded_transfer_locked,
    ROW_BYTES,
    COPIES,
)
from ..era5.transport import NoRedirect
from ..study import load_study_config
from .live import FIELDS, selected_ranges
from .native_source import decode_hour, validate_hour, METRIC_INPUTS

MIB = 1024**2
METHOD = "hrrr-native-utc-day-eight-core-pilot-v1"
CORE_METRICS = tuple(METRIC_INPUTS) + ("WIND_SPEED_10M_MS",)
UNAVAILABLE = ("DEWPOINT_2M_C", "PRECIPITATION_MM")
HOST = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/"


class ArchiveMissing(ValueError):
    pass


class ArchiveError(ValueError):
    pass


def exact_plan(study_config):
    identity = load_study_config(study_config, planning=True)
    if identity is None:
        raise ValueError("Explicit study selection required.")
    west, south, east, north = identity["study_config"]["domain"]["bbox_wgs84"]
    if not (west <= -125.5 < -125 <= east and south <= 49 < 49.5 <= north):
        raise ValueError("Native pilot bbox lies outside the acquisition envelope.")
    requested = identity["requested_time"]
    if not requested["start"] <= "2024-01-01" < "2024-01-02" <= requested["end_exclusive"]:
        raise ValueError("Native pilot day is outside the requested window.")
    package_root = Path(__file__).parents[1]
    source_files = [
        "hourly_weather/native_day.py",
        "hourly_weather/native_source.py",
        "hourly_weather/native_qa.py",
        "hourly_weather/live.py",
        "surface_weather/source.py",
        "surface_weather/source_validation.py",
        "era5/resources.py",
        "era5/transport.py",
        "study.py",
    ]
    runtime = {}
    for name in ("numpy", "pandas", "pyarrow", "eccodes", "pyproj", "shapely"):
        try:
            runtime[name] = version(name)
        except PackageNotFoundError:
            runtime[name] = "not_installed"
    plan = dict(
        method=METHOD,
        source_family="HRRR",
        day="2024-01-01",
        timezone="UTC",
        study_config_sha256=identity["config_sha256"],
        bbox_wgs84=[-125.5, 49, -125, 49.5],
        halo_degrees=0.2,
        producer_source_files_sha256={
            name: hashlib.sha256((package_root / name).read_bytes()).hexdigest()
            for name in source_files
        },
        scientific_runtime=runtime,
        fields=FIELDS,
        cycles=[
            dict(
                valid_time_utc=f"2024-01-01T{h:02}:00:00Z",
                uri=HOST + f"hrrr.20240101/conus/hrrr.t{h:02}z.wrfsfcf00.grib2",
            )
            for h in range(24)
        ],
        limits=vars(
            Limits(
                transfer_bytes=512 * MIB,
                staging_bytes=1024 * MIB,
                memory_bytes=1024 * MIB,
                requests=300,
                seconds=3600,
            )
        ),
        index_reservation=65536,
        max_field_bytes=32 * MIB,
        max_crop_points=4096,
        native_grid_shape=[1799, 1059],
        timeout_seconds=15,
        workers=1,
        initial_processing_target_seconds=900,
        required_core_hours=24,
        nominal_http_requests=216,
        no_precipitation_arm=True,
        decoder_preallocation=dict(
            native_point_limit=2_000_000,
            bytes_per_native_point=256,
            max_encoded_message_bytes=32 * MIB,
            encoded_copy_allowance=2,
            estimated_native_array_working_allowance_bytes=1799 * 1059 * 256 + 2 * 32 * MIB,
            enforcement="measured_RSS_plus_preallocation_and_cooperative_checkpoints_not_OS_preemption",
        ),
        final_h3_eligible=False,
        period_complete=False,
    )
    plan["plan_sha256"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return plan


def _sha(path, budget):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            budget.checkpoint(additional_memory=65536)
            part = stream.read(65536)
            if not part:
                break
            digest.update(part)
    return digest.hexdigest()


class NativeHTTP:
    def __init__(self, budget, plan, *, opener=None):
        self.budget = budget
        self.plan = plan
        self.opener = opener or build_opener(
            ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect()
        )

    def cached_transfer(self, uri, folder, prefix, *, valid, reservation, byte_range=None):
        allowed = {cycle["uri"] for cycle in self.plan["cycles"]}
        if uri not in allowed and uri not in {u + ".idx" for u in allowed}:
            raise ArchiveError("URI is outside exact NOAA pilot objects.")
        binding = dict(
            uri=uri,
            valid_time_utc=valid.isoformat(),
            byte_range=list(byte_range) if byte_range else None,
            reservation=reservation,
            plan_sha256=self.plan["plan_sha256"],
        )
        extension = "grib2" if byte_range else "idx"
        existing = sorted(folder.glob(f"{prefix}-a*.{extension}.source.json"))
        attempts = []
        for meta_path in existing:
            match = re.fullmatch(
                re.escape(prefix) + r"-a(\d+)\." + extension + r"\.source\.json", meta_path.name
            )
            if not match:
                raise ArchiveError("Unrecognized transfer attempt name.")
            attempts.append(int(match[1]))
            metadata = json.loads(meta_path.read_text())
            if any(metadata.get(k) != v for k, v in binding.items()):
                raise ArchiveError("Cached transfer belongs to another immutable request.")
            path = meta_path.with_name(meta_path.name.removesuffix(".source.json"))
            receipt_path = path.with_suffix(path.suffix + ".receipt.json")
            if path.exists():
                receipt = json.loads(receipt_path.read_text())
                if (
                    metadata.get("status") != "HTTP_HEADERS_QUALIFIED"
                    or receipt.get("status") != "COMPLETE"
                    or path.stat().st_size != receipt.get("received_bytes")
                    or _sha(path, self.budget) != receipt.get("sha256")
                ):
                    raise ArchiveError("Cached source bytes differ from their qualified receipt.")
                return (path, metadata), attempts, binding
        return None, attempts, binding

    def transfer(self, uri, folder, prefix, *, valid, reservation, byte_range=None):
        cached, attempts, binding = self.cached_transfer(
            uri, folder, prefix, valid=valid, reservation=reservation, byte_range=byte_range
        )
        if cached is not None:
            return cached
        folder.mkdir(parents=True, exist_ok=True)
        extension = "grib2" if byte_range else "idx"
        attempt = max(attempts, default=-1) + 1
        path = folder / f"{prefix}-a{attempt}.{extension}"
        metadata = dict(binding, status="PLANNED")
        meta_path = path.with_suffix(path.suffix + ".source.json")
        self.budget.check_disk(folder, additional_bytes=8192)
        atomic_json(meta_path, metadata)

        def open_response():
            headers = {
                "Accept-Encoding": "identity",
                "User-Agent": "toolkit-meteorology-native-day-pilot/1",
            }
            if byte_range:
                headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
            remaining = self.budget.limits.seconds - self.budget.receipt()["elapsed_seconds"]
            if remaining <= 0:
                raise LimitExceeded("No HTTP elapsed budget remains.")
            try:
                response = self.opener.open(
                    Request(uri, headers=headers),
                    timeout=min(self.plan["timeout_seconds"], remaining),
                )
            except HTTPError as error:
                code = error.code
                error.close()
                if code == 404 and byte_range is None:
                    raise ArchiveMissing(
                        "Requested NOAA cycle index is unavailable (HTTP 404)."
                    ) from None
                raise ArchiveError(f"NOAA HTTP {code}; body not persisted.") from None
            except Exception:
                raise ArchiveError("NOAA connection failed; details redacted.") from None
            try:
                expected = 206 if byte_range else 200
                length = response.headers.get("Content-Length")
                if (
                    response.status != expected
                    or not length
                    or not length.isdigit()
                    or not 0 < int(length) <= reservation
                    or response.headers.get("Content-Encoding", "identity") != "identity"
                ):
                    raise ArchiveError("NOAA status/length/encoding differs from bounded request.")
                if byte_range:
                    match = re.fullmatch(
                        r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                    )
                    if (
                        not match
                        or tuple(map(int, match.groups()[:2])) != byte_range
                        or int(match[3]) <= byte_range[1]
                        or int(length) != reservation
                    ):
                        raise ArchiveError("NOAA did not honor the exact selected range.")
                etag = response.headers.get("ETag", "")
                if not re.fullmatch(r'"[0-9a-fA-F]{32}(?:-\d+)?"', etag):
                    raise ArchiveError("A qualified S3 source ETag is required.")
                metadata.update(
                    status="HTTP_HEADERS_QUALIFIED",
                    http_status=response.status,
                    content_length=int(length),
                    content_range=response.headers.get("Content-Range"),
                    etag=etag,
                    retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                )
                atomic_json(meta_path, metadata)
                return response
            except BaseException:
                response.close()
                raise

        _bounded_transfer_locked(
            open_response, path, budget=self.budget, reservation_bytes=reservation
        )
        self.budget.persist()
        return path, metadata


def _verify_cached_hour(folder, valid, plan, budget):
    manifest = folder / "HOUR.json"
    if not manifest.exists():
        return None
    evidence = json.loads(manifest.read_text())
    if (
        evidence.get("plan_sha256") != plan["plan_sha256"]
        or evidence.get("valid_time_utc") != valid.isoformat()
    ):
        raise ArchiveError("Committed hour belongs to a different source plan.")
    for artifact in evidence["artifacts"]:
        relative = Path(artifact["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ArchiveError("Hour artifact path escapes owned cache.")
        path = folder / relative
        if not path.is_file() or _sha(path, budget) != artifact["sha256"]:
            raise ArchiveError("Committed native hour cache checksum differs.")
    if evidence.get("grid_path") not in {item["path"] for item in evidence["artifacts"]}:
        raise ArchiveError("Hour grid path is not pinned by its artifact receipt.")
    parquet = folder / evidence["grid_path"]
    rows = pq.ParquetFile(parquet).metadata.num_rows
    if not 0 < rows <= plan["max_crop_points"]:
        raise LimitExceeded("Cached crop exceeds point allowance.")
    budget.checkpoint(additional_memory=rows * ROW_BYTES * COPIES)
    frame = pq.read_table(parquet).to_pandas()
    validate_hour(frame, evidence["decoder"], valid)
    return frame, evidence


def _fetch_hour(cycle, root, http, plan, budget, decoder):
    valid = pd.Timestamp(cycle["valid_time_utc"])
    folder = root / "hours" / valid.strftime("%Y%m%dT%HZ")
    cached = _verify_cached_hour(folder, valid, plan, budget)
    if cached is not None:
        return cached
    index, index_metadata = http.transfer(
        cycle["uri"] + ".idx", folder, "index", valid=valid, reservation=plan["index_reservation"]
    )
    text = index.read_text(encoding="ascii")
    # Bind inventory to requested cycle and f00 semantics before downloading fields.
    if any(f"d={valid:%Y%m%d%H}:" not in line for line in text.splitlines() if line.strip()):
        raise ArchiveError("Index cycle identity differs from requested UTC hour.")
    ranges = selected_ranges(text)
    if any(b - a + 1 > plan["max_field_bytes"] for a, b in ranges):
        raise LimitExceeded("Selected nationwide field exceeds 32 MiB source cap.")
    # selected_ranges sorts by offsets; resolve field name independently from inventory.
    lines = [line for line in text.splitlines() if line.strip()]
    offsets = [int(line.split(":", 2)[1]) for line in lines]
    planned = []
    for name, selector in FIELDS.items():
        matches = [i for i, line in enumerate(lines) if selector in line]
        if len(matches) != 1:
            raise ArchiveError("Ambiguous native field identity in index.")
        i = matches[0]
        span = (offsets[i], offsets[i + 1] - 1)
        if not any(marker in lines[i].split(":") for marker in ("anl", "0 hour fcst")):
            raise ArchiveError("Selected index field is not an instantaneous f00 analysis.")
        cached, _, _ = http.cached_transfer(
            cycle["uri"],
            folder,
            name,
            valid=valid,
            reservation=span[1] - span[0] + 1,
            byte_range=span,
        )
        planned.append((name, span, cached))
    # Validate all reusable fields before reserving or opening any outstanding one.
    outstanding = [span for _, span, cached in planned if cached is None]
    if (
        budget.reserved + sum(b - a + 1 for a, b in outstanding) > budget.limits.transfer_bytes
        or budget.requests + len(outstanding) > budget.limits.requests
    ):
        raise LimitExceeded("Remaining budget cannot cover outstanding source cycle transfers.")
    files = {}
    source_artifacts = []
    etags = set()
    for name, span, cached in planned:
        field, meta = (
            cached
            if cached is not None
            else http.transfer(
                cycle["uri"],
                folder,
                name,
                valid=valid,
                reservation=span[1] - span[0] + 1,
                byte_range=span,
            )
        )
        files[name] = field
        etags.add(meta["etag"])
        for path in (
            field,
            field.with_suffix(field.suffix + ".source.json"),
            field.with_suffix(field.suffix + ".receipt.json"),
        ):
            source_artifacts.append(dict(path=path.name, sha256=_sha(path, budget)))
    if len(etags) != 1:
        raise ArchiveError("Selected field transfers represent different S3 object generations.")
    frame, evidence = decoder(
        files, valid=valid, bbox=plan["bbox_wgs84"], halo=plan["halo_degrees"], budget=budget
    )
    validate_hour(frame, evidence, valid)
    budget.check_disk(folder, additional_bytes=len(frame) * ROW_BYTES + 512 * 1024)
    number = len(list(folder.glob("grid-a*.parquet")))
    parquet = folder / f"grid-a{number}.parquet"
    with parquet.open("xb") as stream:
        pq.write_table(
            pa.Table.from_pandas(frame, preserve_index=False), stream, compression="zstd"
        )
        stream.flush()
        os.fsync(stream.fileno())
    for path in (
        index,
        index.with_suffix(index.suffix + ".source.json"),
        index.with_suffix(index.suffix + ".receipt.json"),
        parquet,
    ):
        source_artifacts.append(dict(path=path.name, sha256=_sha(path, budget)))
    committed = dict(
        plan_sha256=plan["plan_sha256"],
        valid_time_utc=valid.isoformat(),
        grid_path=parquet.name,
        decoder=evidence,
        artifacts=source_artifacts,
        status="COMPLETE_NATIVE_HOUR_NOT_H3_RELEASE",
    )
    atomic_json(folder / "HOUR.json", committed)
    return frame, committed


def native_daily(frames, day, budget):
    if not frames:
        raise ValueError("No native point universe exists for a daily checkpoint.")
    points = frames[0][["SOURCE_GRID_INDEX", "SOURCE_LAT", "SOURCE_LON", "SOURCE_GRID_HASH"]].copy()
    for frame in frames:
        if not frame[["SOURCE_GRID_INDEX", "SOURCE_LAT", "SOURCE_LON", "SOURCE_GRID_HASH"]].equals(
            points
        ):
            raise ValueError("Native point universe/grid changed across UTC hours.")
    budget.checkpoint(additional_memory=sum(len(f) for f in frames) * 512 * COPIES)
    hourly = pd.concat(frames, ignore_index=True)
    if hourly.duplicated(["VALID_TIME_UTC", "SOURCE_GRID_INDEX"]).any():
        raise ValueError("Duplicate UTC hour/native-point keys.")
    daily = points.copy()
    daily.insert(0, "DATE", day)
    daily["TIMEZONE"] = "UTC"
    daily["SOURCE_MODEL"] = "HRRR"
    daily["AVAILABILITY_POLICY"] = "historical_publication_unknown_actual_retrieval_only"
    daily["AVAILABLE_AT_UTC"] = None
    for metric in CORE_METRICS:
        group = hourly.groupby("SOURCE_GRID_INDEX")[metric]
        count = group.count().reindex(points.SOURCE_GRID_INDEX).to_numpy()
        mean = group.mean().reindex(points.SOURCE_GRID_INDEX).to_numpy()
        prefix = "HRRR_" + metric
        daily[prefix] = np.where(count == 24, mean, np.nan)
        daily[prefix + "_VALID_HOURS"] = count
        daily[prefix + "_EXPECTED_HOURS"] = 24
        daily[prefix + "_STATUS"] = np.where(
            count == 24, "COMPLETE", np.where(count > 0, "PARTIAL", "UNAVAILABLE")
        )
        daily[prefix + "_COVERAGE_FRACTION"] = count / 24
    for metric in UNAVAILABLE:
        prefix = "HRRR_" + metric
        daily[prefix] = np.nan
        daily[prefix + "_VALID_HOURS"] = 0
        daily[prefix + "_EXPECTED_HOURS"] = 24
        daily[prefix + "_STATUS"] = "UNAVAILABLE"
        daily[prefix + "_COVERAGE_FRACTION"] = 0.0
    return hourly, daily


def run_day(study_config, plan, output, *, opener=None, decoder=decode_hour, budget=None):
    if plan != exact_plan(study_config):
        raise ValueError("Plan differs from exact source/day/bbox/caps/runtime/producer pins.")
    if plan["scientific_runtime"]["eccodes"] == "not_installed":
        raise ValueError("Native GRIB codec required before any provider call.")
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".transfer.lock").open("a+b") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another worker owns the native day.") from None
        budget = budget or Budget(Limits(**plan["limits"]), staging_root=root)
        if vars(budget.limits) != plan["limits"] or budget.staging_root != root.resolve():
            raise ValueError("Whole-day budget must bind exact caps and aggregate owned root.")
        budget.bind_journal(root / "TRANSFER_BUDGET.json")
        if (root / "PLAN.json").exists() and json.loads((root / "PLAN.json").read_text()) != plan:
            raise ValueError("Output is bound to a different immutable plan.")
        atomic_json(root / "PLAN.json", plan)
        http = NativeHTTP(budget, plan, opener=opener)
        frames = []
        hours = []
        missing = []
        try:
            # Verify every committed hour before any new provider request.
            for cycle in plan["cycles"]:
                valid = pd.Timestamp(cycle["valid_time_utc"])
                _verify_cached_hour(
                    root / "hours" / valid.strftime("%Y%m%dT%HZ"), valid, plan, budget
                )
            for cycle in plan["cycles"]:
                try:
                    frame, evidence = _fetch_hour(cycle, root, http, plan, budget, decoder)
                except ArchiveMissing:
                    missing.append(cycle["valid_time_utc"])
                    continue
                frames.append(frame)
                hours.append(evidence)
            hourly, daily = native_daily(frames, plan["day"], budget)
            from .native_qa import independent_qa

            qa = independent_qa(hourly, daily, plan, hours, budget=budget)
            if (root / "MANIFEST.json").exists():
                previous = json.loads((root / "MANIFEST.json").read_text())
                for item in previous["artifacts"]:
                    if _sha(root / item["path"], budget) != item["sha256"]:
                        raise ArchiveError("Completed native day artifact changed.")
                return root / "MANIFEST.json"
            checkpoint = len(list(root.glob("native-hourly-a*.parquet")))
            artifacts = []
            for name, frame in [("native-hourly", hourly), ("native-daily", daily)]:
                path = root / f"{name}-a{checkpoint}.parquet"
                budget.check_disk(root, additional_bytes=len(frame) * ROW_BYTES)
                with path.open("xb") as stream:
                    pq.write_table(
                        pa.Table.from_pandas(frame, preserve_index=False),
                        stream,
                        compression="zstd",
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                artifacts.append(
                    dict(path=path.name, sha256=_sha(path, budget), bytes=path.stat().st_size)
                )
            qa_path = root / f"QA-a{checkpoint}.json"
            atomic_json(qa_path, qa)
            artifacts.append(
                dict(path=qa_path.name, sha256=_sha(qa_path, budget), bytes=qa_path.stat().st_size)
            )
            state = dict(
                status="PARTIAL_NATIVE_CHECKPOINT_NOT_PUBLISHED"
                if missing
                else "COMPLETE_NATIVE_DAY_NOT_H3_RELEASE",
                actual_hours=len(hours),
                missing_utc_hours=missing,
                artifacts=artifacts,
                qa=qa,
                budget=budget.receipt(),
            )
            budget.check_disk(root, additional_bytes=len(json.dumps(state).encode()) + 8192)
            atomic_json(root / "RUN_STATE.json", state)
            if missing:
                return root / "RUN_STATE.json"
            manifest = dict(
                state,
                plan=plan,
                source_family="HRRR",
                hours=hours,
                day=plan["day"],
                final_h3_eligible=False,
                period_complete=False,
                method=METHOD,
            )
            budget.check_disk(root, additional_bytes=len(json.dumps(manifest).encode()) + 8192)
            atomic_json(root / "MANIFEST.json", manifest)
            return root / "MANIFEST.json"
        except BaseException:
            atomic_json(
                root / "RUN_STATE.json",
                dict(
                    status="INCOMPLETE_NOT_PUBLISHED",
                    actual_hours=len(hours),
                    missing_utc_hours=missing,
                    budget=budget.receipt(),
                ),
            )
            raise
        finally:
            budget.persist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "run"])
    parser.add_argument("--study-config", required=True)
    parser.add_argument("--plan-json")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.action == "plan":
            print(json.dumps(exact_plan(args.study_config), indent=2))
            return
        if not args.plan_json or not args.output:
            parser.error("run requires --plan-json and --output")
        result = run_day(
            args.study_config, json.loads(Path(args.plan_json).read_text()), args.output
        )
        print(json.dumps({"checkpoint": str(result), "final_h3_eligible": False}))
    except Exception as error:
        print(
            json.dumps({"status": "INCOMPLETE_NOT_PUBLISHED", "error_class": type(error).__name__})
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
