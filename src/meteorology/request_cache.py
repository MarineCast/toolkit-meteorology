"""Immutable, checksum-backed request results and shared source caches."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta, date
import fcntl
from importlib.util import find_spec
from importlib.metadata import version, PackageNotFoundError

from ._version import __version__
import json
import os
from pathlib import Path
import uuid

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .artifacts import checksum_path
from .era5.resources import Budget, atomic_json, ROW_BYTES
from .request import digest


class CacheError(ValueError):
    pass


FLOATS = {"VALUE", "SOURCE_LAT", "SOURCE_LON", "SOURCE_GRID_DISTANCE_M", "COVERAGE_FRACTION"}
INTS = {"SOURCE_GRID_INDEX", "H3_RESOLUTION", "VALID_HOURS", "EXPECTED_HOURS"}
COLUMNS = [
    "DATE",
    "H3_INDEX",
    "H3_RESOLUTION",
    "SOURCE_GRID_INDEX",
    "SOURCE_LAT",
    "SOURCE_LON",
    "SOURCE_GRID_HASH",
    "SOURCE_GRID_DISTANCE_M",
    "VALID_TIME_UTC",
    "METRIC",
    "VALUE",
    "UNITS",
    "SOURCE",
    "STATUS",
    "VALID_HOURS",
    "EXPECTED_HOURS",
    "COVERAGE_FRACTION",
    "TIMEZONE",
    "INTERVAL_START_UTC",
    "INTERVAL_END_UTC",
    "METHOD",
    "REDUCTION",
    "WIND_VECTOR_BASIS",
    "RETRIEVED_AT_UTC",
    "SOURCE_SHA256_RECEIPTS",
    "CONSOLIDATION_STATUS",
    "AVAILABLE_AT_UTC",
    "AVAILABILITY_POLICY",
    "EVIDENCE_KIND",
]
RESULT_SCHEMA = pa.schema(
    [
        pa.field(c, pa.float64() if c in FLOATS else pa.int64() if c in INTS else pa.string())
        for c in COLUMNS
    ]
)


def result_table(frame, metadata=None):
    frame = frame.reindex(columns=COLUMNS)
    schema = RESULT_SCHEMA
    if metadata is not None:
        schema = schema.with_metadata(
            {
                b"meteorology.manifest": json.dumps(
                    metadata, sort_keys=True, allow_nan=False
                ).encode()
            }
        )
    return pa.Table.from_pandas(frame, schema=schema, preserve_index=False)


def _safe_path(directory, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise CacheError("Cache artifact path escapes its result directory.")
    return directory / path


def _validate(directory, manifest, *, selected=None):
    if manifest.get("manifest_sha256") != digest(
        {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    ):
        raise CacheError("Result manifest is corrupt; inspect cache or request refresh.")
    for entry in manifest["artifacts"] if selected is None else selected:
        path = _safe_path(directory, entry["path"])
        if (
            not path.is_file()
            or path.stat().st_size != entry["bytes"]
            or checksum_path(path) != entry["sha256"]
        ):
            raise CacheError("Result checksum is corrupt; retained evidence was not replaced.")
        file = pq.ParquetFile(path)
        if file.metadata.num_rows != entry["rows"] or not file.schema_arrow.equals(
            RESULT_SCHEMA, check_metadata=False
        ):
            raise CacheError("Result row count/schema differs from committed manifest.")
    for entry in manifest["inputs"]:
        path = Path(entry["path"])
        if not path.is_file() or checksum_path(path) != entry["sha256"]:
            raise CacheError(
                "Retained source receipt is corrupt or absent; request refresh in a new generation."
            )


def _namespace(plan):
    return plan["cache_dir"] / (
        "fixtures" if plan["request"]["evidence_kind"] == "synthetic_fixture" else "real"
    )


def _compatible(existing, requested):
    changing = {"variables", "inputs", "start", "end_exclusive"}
    return (
        all(existing.get(k) == v for k, v in requested.items() if k not in changing)
        and set(requested["variables"]) <= set(existing["variables"])
        and existing["start"]
        <= requested["start"]
        < requested["end_exclusive"]
        <= existing["end_exclusive"]
    )


def _find(plan):
    root = _namespace(plan)
    candidates = []
    for path in (root / "results").glob("*/MANIFEST.json"):
        try:
            manifest = json.loads(path.read_text())
        except (OSError, ValueError):
            raise CacheError(
                "Unreadable final-result manifest; inspect cache or request refresh."
            ) from None
        if manifest.get("status") == "COMPLETE" and _compatible(
            manifest.get("request", {}), plan["request"]
        ):
            candidates.append((path, manifest))
    candidates.sort(key=lambda pair: sum(a["rows"] for a in pair[1]["artifacts"]))
    if not candidates:
        return None
    path, manifest = candidates[0]
    wanted = [
        a
        for a in manifest["artifacts"]
        if a["metric"] in plan["metrics"]
        and plan["request"]["start"] <= a["day"] < plan["request"]["end_exclusive"]
    ]
    _validate(path.parent, manifest, selected=wanted)
    return WeatherResult(
        path.parent, manifest, request=plan["request"], selected=wanted, cache_hit=True
    )


class WeatherResult:
    """Disk-backed native/H3 long data. pandas is materialized only on request."""

    def __init__(self, directory, manifest, *, request=None, selected=None, cache_hit=False):
        self.directory = Path(directory)
        self._manifest = deepcopy(manifest)
        self._selected = deepcopy(manifest["artifacts"] if selected is None else selected)
        self._request = deepcopy(request or manifest["request"])
        self.cache_hit = cache_hit

    @property
    def metadata(self):
        metadata = deepcopy(self._manifest)
        metadata.update(
            request=self._request,
            artifacts=self._selected,
            row_count=sum(a["rows"] for a in self._selected),
            cache_hit=self.cache_hit,
            selection_from_result_id=self._manifest["result_id"],
        )
        metadata["metrics"] = {
            m: v
            for m, v in metadata["metrics"].items()
            if m in {a["metric"] for a in self._selected}
        }
        metadata.pop("manifest_sha256", None)  # the view has its own durable identity
        metadata["view_sha256"] = digest(metadata)
        return metadata

    def _tables(self):
        _validate(self.directory, self._manifest, selected=self._selected)
        for entry in self._selected:
            file = pq.ParquetFile(_safe_path(self.directory, entry["path"]))
            for batch in file.iter_batches(batch_size=4096):
                yield pa.Table.from_batches([batch]).cast(RESULT_SCHEMA)

    def to_pandas(self):
        frames = [table.to_pandas() for table in self._tables()]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)

    def to_parquet(self, path):
        """Stream to an atomic Parquet with complete durable JSON metadata."""
        path = Path(path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Explicit output overwrite is caller scope; never export over retained evidence.
        if path.is_relative_to(self.directory.parents[1]) or any(
            path == Path(r["path"]) for r in self._manifest["inputs"]
        ):
            raise ValueError("Output would replace immutable result/source evidence.")
        temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".partial")
        schema = RESULT_SCHEMA.with_metadata(
            {
                b"meteorology.manifest": json.dumps(
                    self.metadata, sort_keys=True, allow_nan=False
                ).encode()
            }
        )
        try:
            with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
                for table in self._tables():
                    writer.write_table(table.replace_schema_metadata(schema.metadata))
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException:
            # Keep a failed export inspectable; no successful destination was published.
            raise
        return path


def preview_request(plan, *, refresh=False):
    hit = None if refresh else _find(plan)
    from .era5.credentials import resolve_credentials

    return dict(
        request=plan["request"],
        required_inputs=plan["required_inputs"],
        metrics=plan["metrics"],
        time_boundaries=plan["time_boundaries"],
        accumulation_boundary=plan["accumulation_boundary"],
        spatial_support=dict(
            output=plan["request"]["spatial_output"],
            bbox=plan["request"]["bbox"],
            h3_cells=len(plan["support"]) if plan["support"] is not None else None,
            estimated_native_points=plan["estimated_native_points"],
        ),
        cache=dict(
            final_result_hit=hit is not None,
            provider_requests_if_hit=0,
            missing_work=[]
            if hit
            else [
                "selected source acquisition/receipt validation",
                "normalization",
                "UTC reductions",
            ],
        ),
        expected_requests=plan["jobs"]
        if plan["jobs"]
        else dict(
            cycles=(
                date.fromisoformat(plan["request"]["end_exclusive"])
                - date.fromisoformat(plan["request"]["start"])
            ).days
            * 24,
            f00_fields=plan["required_inputs"],
            index_then_exact_field_ranges=True,
        ),
        nominal_provider_requests=0 if hit else plan["nominal_provider_requests"],
        transfer_size=plan["transfer_size"],
        limits=vars(plan["limits"]),
        memory_preflight=plan["memory_preflight"],
        access=resolve_credentials(env_file=plan["credential_env_file"], inspect_only=True).public()
        if plan["request"]["source"] == "era5"
        else {"credential_required": False},
        production_shared_study_release=False,
        network_requests_made=0,
    )


def execute_request(plan, *, refresh=False, source_files=None, retrieved_at_utc=None):
    root = _namespace(plan)
    root.mkdir(parents=True, exist_ok=True)
    # Namespace lease serializes source writers too. Concurrent compatible callers
    # wait for the first terminal commit, then read it with zero provider requests.
    with (root / ".request.lock").open("a+b") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        if not refresh:
            hit = _find(plan)
            if hit is not None:
                return hit
        if find_spec("eccodes") is None:
            raise ImportError(
                "Install toolkit-meteorology[native-pilot] for HRRR or [era5] for ERA5 GRIB decoding."
            )
        if source_files is None and plan["nominal_provider_requests"] > plan["limits"].requests:
            raise ValueError("Missing acquisition work exceeds request cap; use a shorter request.")
        token = uuid.uuid4().hex
        run_root = root / "runs" / (plan["request_id"] if not refresh else token)
        run_root.mkdir(parents=True, exist_ok=True)
        budget = Budget(plan["limits"], staging_root=root)
        budget.bind_journal(run_root / "TRANSFER_BUDGET.json")
        from .request_adapters import hrrr_day, era5_day, reduce_day

        artifacts, inputs, grids = [], [], set()
        forecast_support = []
        stage = run_root / ("result-" + token)
        stage.mkdir()
        day = date.fromisoformat(plan["request"]["start"])
        last = date.fromisoformat(plan["request"]["end_exclusive"])
        geometry, mappings = {}, {}
        complete = True
        try:
            atomic_json(
                run_root / "RUN_STATE.json", dict(status="ACQUIRING", request=plan["request"])
            )
            while day < last:
                day_text = day.isoformat()
                common = dict(
                    source_files=source_files,
                    retrieved_at_utc=retrieved_at_utc,
                    generation=token if refresh else "retained",
                )
                if plan["request"]["source"] == "hrrr":
                    hourly, receipts = hrrr_day(
                        plan, day_text, root, budget, geometry=geometry, mappings=mappings, **common
                    )
                else:
                    hourly, receipts = era5_day(plan, day_text, root, run_root, budget, **common)
                forecast_support.extend(hourly.attrs.get("source_forecast_support", []))
                inputs.extend(receipts)
                grids.update(hourly.SOURCE_GRID_HASH)
                for metric, frame in reduce_day(hourly, plan, day_text):
                    frame["EVIDENCE_KIND"] = plan["request"]["evidence_kind"]
                    complete &= bool((frame.STATUS == "COMPLETE").all())
                    path = stage / f"{day_text}.{metric}.parquet"
                    budget.check_disk(root, additional_bytes=len(frame) * ROW_BYTES)
                    pq.write_table(result_table(frame), path, compression="zstd")
                    artifacts.append(
                        dict(
                            path=path.name,
                            day=day_text,
                            metric=metric,
                            rows=len(frame),
                            bytes=path.stat().st_size,
                            sha256=checksum_path(path),
                        )
                    )
                day += timedelta(days=1)
            # Canonical derived identity binds the actual source receipts/grids,
            # requested reductions/support, scientific producer and schema versions.
            unique_inputs = sorted({json.dumps(r, sort_keys=True) for r in inputs})
            inputs = [json.loads(r) for r in unique_inputs]
            identity = digest(
                dict(request=plan["request"], inputs=inputs, source_grids=sorted(grids))
            )
            runtime = {}
            for name in ("numpy", "pandas", "pyarrow", "h3", "pyproj", "shapely", "eccodes"):
                try:
                    runtime[name] = version(name)
                except PackageNotFoundError:
                    runtime[name] = None
            manifest = dict(
                software_version=__version__,
                scientific_runtime=runtime,
                source_forecast_support=forecast_support,
                result_id=identity,
                request=plan["request"],
                metrics=plan["metrics"],
                status="COMPLETE" if complete else "PARTIAL",
                artifacts=artifacts,
                inputs=inputs,
                source_grids=sorted(grids),
                row_count=sum(a["rows"] for a in artifacts),
                budget=budget.receipt(),
                source_resolution="HRRR 3 km"
                if plan["request"]["source"] == "hrrr"
                else "ERA5 0.25 degree CDS distribution; ~31 km model information",
                spatial_method="nearest-haversine-decoded-footprint-v1"
                if plan["request"]["spatial_output"] == "h3"
                else "native_grid_points_inside_bbox_v1",
                production_shared_study_release=False,
                limitations=[
                    "Retrospective source processing, no independent regional accuracy validation.",
                    "Daily means are sampled hourly values; partial days stay null.",
                    "Snowfall is interval water equivalent, not ground snow depth or snowpack.",
                    "Actual historical publication times are unknown.",
                ],
            )
            manifest["manifest_sha256"] = digest(manifest)
            _validate(stage, manifest)
            budget.check_disk(root, additional_bytes=len(json.dumps(manifest).encode()) + 8192)
            for entry in artifacts:
                with (stage / entry["path"]).open("rb") as stream:
                    os.fsync(stream.fileno())
            atomic_json(stage / "MANIFEST.json", manifest)
            destination = root / "results" / identity
            destination.parent.mkdir(exist_ok=True)
            if destination.exists():
                previous = json.loads((destination / "MANIFEST.json").read_text())
                _validate(destination, previous)
                # Preserve both attempts; never replace immutable terminal artifacts.
                result = WeatherResult(destination, previous)
            else:
                os.rename(stage, destination)
                descriptor = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                result = WeatherResult(destination, manifest)
            atomic_json(
                run_root / "RUN_STATE.json",
                dict(status="COMMITTED_" + manifest["status"], result_id=identity),
            )
            return result
        except BaseException:
            atomic_json(
                run_root / "RUN_STATE.json",
                dict(status="INCOMPLETE_NOT_PUBLISHED", budget=budget.receipt()),
            )
            raise
        finally:
            budget.persist()
