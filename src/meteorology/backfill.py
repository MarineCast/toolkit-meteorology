"""ERA5 historical backfill: bounded monthly jobs, independent leases and partitions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import date, timedelta
import json
import os
from pathlib import Path
import shutil
import time
import uuid

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .artifacts import checksum_path
from .backfill_plan import DEFAULT_VARIABLES, METHOD, SCHEMA, plan_backfill, split_chunk
from .backfill_compute import normalize, read_cube, mapping, day_frames
from .backfill_resources import BackfillBudget, RunResources, lease, charge_failures
from .backfill_metrics import MeasuredProvider
from .era5.credentials import resolve_credentials
from .era5.jobs import run_job, request_identity, AmbiguousSubmission
from .era5.resources import atomic_json, LimitExceeded, TransferTooLarge
from .era5.transport import CDSHTTPProvider, TransientTransportError, RequestTooLarge
from .request import digest
from .request_lifecycle import PublicationUncertainError, secondary

SOURCE_CONTEXT = digest(dict(backend="CDS", dataset="reanalysis-era5-single-levels", version=1))


def signed(data):
    result = dict(data)
    result.setdefault("version", 1)
    result.pop("sha256", None)
    result["sha256"] = digest(result)
    return result


def record(path):
    value = json.loads(Path(path).read_text())
    if value.get("version") != 1 or value.get("sha256") != digest(
        {k: v for k, v in value.items() if k != "sha256"}
    ):
        raise ValueError(f"Backfill record checksum invalid; preserve and reconcile: {path}")
    return value


def sync_file(path):
    with Path(path).open("rb") as stream:
        os.fsync(stream.fileno())


def confirm_pointer(path):
    sync_file(path)
    descriptor = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_source(value):
    source = Path(value["path"])
    if (
        not source.is_file()
        or source.stat().st_size != value["bytes"]
        or checksum_path(source) != value["sha256"]
    ):
        raise ValueError(
            "Retained ERA5 source is absent/corrupt; selective reacquisition required."
        )


def compatible_source(existing, requested):
    fixed = set(requested) - {"variable", "day", "time"}
    return all(existing.get(key) == requested[key] for key in fixed) and all(
        set(requested[key]) <= set(existing.get(key, [])) for key in ("variable", "day", "time")
    )


def acquire(chunk, budget, plan, *, credential_env_file, retries, provider_factory=None):
    """One source lease prevents duplicate submissions, independent of other chunks."""
    selection = chunk["request"]
    source_id = request_identity(selection, study_sha256=SOURCE_CONTEXT)
    family = budget.namespace / "sources" / f"{selection['year'][0]}-{selection['month'][0]}"
    family.mkdir(parents=True, exist_ok=True)
    destination = family / source_id
    with budget.worker_lock, lease(destination / ".source.lock"), ExitStack() as held:
        source = None
        cached = None
        selected = selection
        candidates = sorted(family.glob("*/SOURCE.json"))
        candidates.sort(key=lambda p: p.parent.name != source_id)
        for candidate in candidates:
            value = record(candidate)
            if compatible_source(value["selection"], selection):
                try:
                    validate_source(value["source"])
                except ValueError:
                    # Preserve corrupt immutable bytes and acquire a new attempt.
                    if candidate.parent == destination:
                        continue
                    raise
                if candidate.parent != destination:
                    held.enter_context(lease(candidate.parent / ".source.lock"))
                    value = record(candidate)
                    validate_source(value["source"])
                source, selected, cached = value["source"], value["selection"], value
                destination = candidate.parent
                source_id = value["source_id"]
                break
        state_path = destination / "ATTEMPT.json"
        if source is None:
            if state_path.exists():
                state = record(state_path)
                if state["owner"] != budget.owner:
                    raise ValueError(
                        "Unfinished source attempt belongs to another run/worker; resume its original caps."
                    )
                attempt = destination / state["attempt"]
            else:
                attempt = destination / "attempts" / uuid.uuid4().hex
                attempt.mkdir(parents=True)
                budget.write_json(
                    state_path,
                    signed(dict(owner=budget.owner, attempt=str(attempt.relative_to(destination)))),
                )
            if (attempt / "JOB_STATE.json").exists():
                old_state = json.loads((attempt / "JOB_STATE.json").read_text())
                status = old_state["status"]
                if status == "COMPLETE_DOWNLOAD_NOT_SCIENTIFICALLY_QUALIFIED":
                    try:
                        validate_source(
                            dict(
                                path=str(attempt / "source.grib"),
                                bytes=old_state["bytes"],
                                sha256=old_state["sha256"],
                            )
                        )
                    except ValueError:
                        status = "INVALID_DOWNLOAD"
                if status in {"FAILED", "DOWNLOAD_INCOMPLETE", "INVALID_DOWNLOAD"}:
                    attempt = destination / "attempts" / uuid.uuid4().hex
                    attempt.mkdir(parents=True)
                    budget.write_json(
                        state_path,
                        signed(
                            dict(owner=budget.owner, attempt=str(attempt.relative_to(destination)))
                        ),
                    )
                    if status in {"DOWNLOAD_INCOMPLETE", "INVALID_DOWNLOAD"} and old_state.get(
                        "job_id"
                    ):
                        budget.write_json(
                            attempt / "JOB_STATE.json", dict(old_state, status="SUBMITTED")
                        )
            provider = getattr(budget, "provider", None)
            complete = (attempt / "JOB_STATE.json").exists() and json.loads(
                (attempt / "JOB_STATE.json").read_text()
            )["status"] == "COMPLETE_DOWNLOAD_NOT_SCIENTIFICALLY_QUALIFIED"
            if not complete:
                budget.prepare_download(attempt / "source.grib.partial", plan["source_cap"])
            if not complete and provider is None:
                if provider_factory is None:
                    credential = resolve_credentials(env_file=credential_env_file, required=True)
                    provider = CDSHTTPProvider(
                        key=credential.key,
                        budget=budget,
                        storage_hosts=("object-store.os-api.cci2.ecmwf.int",),
                        download_cap=plan["source_cap"],
                    )
                else:
                    provider = provider_factory(budget=budget, download_cap=plan["source_cap"])
                # Existing acceptance is inspected once per active worker.
                for retry in range(retries + 1):
                    try:
                        provider.verify_terms([dict(id="cc-by", revision=1)])
                        break
                    except (TimeoutError, ConnectionError, TransientTransportError):
                        if retry == retries:
                            raise
                        time.sleep(min(2**retry, 30))
                        budget.checkpoint()
                budget.provider = provider
            provider = (
                MeasuredProvider(provider, budget.resources) if provider is not None else None
            )
            for retry in range(retries + 1):
                try:
                    receipt = run_job(
                        provider,
                        selection,
                        attempt,
                        study_sha256=SOURCE_CONTEXT,
                        budget=budget,
                        approved_identity=request_identity(selection, study_sha256=SOURCE_CONTEXT),
                        download_reservation=plan["source_cap"],
                        journal_path=budget.journal,
                        poll_seconds=15,
                        poll_window_seconds=900,
                        max_polls=60,
                    )
                    break
                except AmbiguousSubmission:
                    raise  # Never resubmit an ambiguous job.
                except (TimeoutError, ConnectionError, TransientTransportError):
                    state = (
                        json.loads((attempt / "JOB_STATE.json").read_text())
                        if (attempt / "JOB_STATE.json").exists()
                        else {}
                    )
                    if state.get("status") == "SUBMITTING_NO_DURABLE_ID":
                        raise AmbiguousSubmission(
                            "Submission outcome is unknown; reconcile with CDS before retrying."
                        ) from None
                    if retry == retries:
                        raise
                    if state.get("status") == "DOWNLOAD_INCOMPLETE" and state.get("job_id"):
                        # Retry the known job into new immutable storage; old partials
                        # and all consumed counters remain retained.
                        attempt = destination / "attempts" / uuid.uuid4().hex
                        attempt.mkdir(parents=True)
                        budget.write_json(
                            state_path,
                            signed(
                                dict(
                                    owner=budget.owner,
                                    attempt=str(attempt.relative_to(destination)),
                                )
                            ),
                        )
                        budget.write_json(
                            attempt / "JOB_STATE.json", dict(state, status="SUBMITTED")
                        )
                        budget.prepare_download(attempt / "source.grib.partial", plan["source_cap"])
                        state = dict(state, status="SUBMITTED")
                    if state.get("status") not in {"SUBMITTED", "POLL_TIMEOUT_RESUMABLE"}:
                        raise
                    time.sleep(min(2**retry, 30))
                    budget.checkpoint()
            source = dict(
                path=str(attempt / "source.grib"),
                bytes=receipt["bytes"],
                sha256=receipt["sha256"],
                retrieved_at_utc=receipt["retrieved_at_utc"],
                source_id=source_id,
                backend="CDS",
                dataset="reanalysis-era5-single-levels",
                selection=selected,
                uri="cds://reanalysis-era5-single-levels/" + source_id,
            )
        if (
            cached
            and cached.get("normalized_owner") == budget.owner
            and cached.get("normalized")
            and Path(cached["normalized"]).is_file()
        ):
            cube_path = Path(cached["normalized"])
            try:
                stored = read_cube(cube_path)
            except (ValueError, OSError):
                stored = None  # Derived scratch can be rebuilt from verified raw evidence.
            if stored is None or not compatible_source(
                stored["metadata"].get("selection", {}), selection
            ):
                cube_path = normalize(
                    source,
                    selection,
                    budget.run_root / "scratch" / (uuid.uuid4().hex + ".npz"),
                    budget,
                    validated_subset=True,
                )
        else:
            scratch = budget.run_root / "scratch" / (uuid.uuid4().hex + ".npz")
            begin = time.perf_counter()
            cube_path = normalize(
                source, selection, scratch, budget, validated_subset=cached is not None
            )
            budget.resources.metric("decode_normalize_seconds", time.perf_counter() - begin)
            budget.resources.metric("decodes", 1)
        # Persist the first midnight separately so the next chunk is decoded once.
        import numpy as np

        cube = read_cube(cube_path)
        edge = destination / "edges" / (source["sha256"] + "-" + chunk["start"] + ".npz")
        edge.parent.mkdir(parents=True, exist_ok=True)
        accumulated = [p for p in cube["parameters"] if p in {"tp", "sf"}]
        if accumulated and not edge.exists():
            positions = [list(cube["parameters"]).index(p) for p in accumulated]
            h = list(cube["times"]).index(pd.Timestamp(chunk["start"], tz="UTC").value)
            values = cube["values"][positions, h : h + 1]
            with budget.write_file(edge, values.nbytes + 1024**2):
                with edge.open("xb") as stream:
                    np.savez_compressed(
                        stream,
                        values=values,
                        revisions=cube["revisions"][positions, h : h + 1],
                        coordinates=cube["coordinates"],
                        times=cube["times"][h : h + 1],
                        parameters=np.array(accumulated),
                    )
            evidence = {
                f"{i}/0": cube["metadata"]["evidence"][f"{p}/{h}"]
                for i, p in enumerate(positions)
                if f"{p}/{h}" in cube["metadata"]["evidence"]
            }
            budget.write_json(
                edge.with_suffix(".json"),
                signed(
                    dict(cube["metadata"], evidence=evidence, artifact_sha256=checksum_path(edge))
                ),
            )
        budget.write_json(
            destination / "SOURCE.json",
            signed(
                dict(
                    version=1,
                    selection=selected,
                    source_id=source_id,
                    source=source,
                    normalized=str(cube_path),
                    normalized_owner=budget.owner,
                )
            ),
        )
        budget.retain_directory(destination)
        return dict(
            cube=cube_path,
            edge=edge if accumulated else None,
            source=source,
            selection=selected,
            chunk=chunk,
            budget=budget,
        )


def acquire_adaptive(chunk, budget, plan, *, output=None, **options):
    """Split explicit provider size rejection, retaining attempts and charged counters."""
    selection = chunk["request"]
    identity = request_identity(selection, study_sha256=SOURCE_CONTEXT)
    directory = (
        budget.namespace / "sources" / f"{selection['year'][0]}-{selection['month'][0]}" / identity
    )
    split = directory / "SPLIT.json"
    if output is not None and not chunk.get("boundary"):
        key = digest(dict(request=plan["request"], start=chunk["start"], end=chunk["end"]))
        pointer = output / "partitions" / chunk["start"][:7] / key / "CURRENT.json"
        if pointer.exists():
            completed = record(pointer)
            if valid_partition(completed, output):
                confirm_pointer(pointer)
                source = completed["sources"][0]
                edge = (
                    Path(source["path"]).parents[2]
                    / "edges"
                    / (source["sha256"] + "-" + chunk["start"] + ".npz")
                )
                if not plan["boundary"] or edge.exists():
                    return [
                        dict(
                            cube=None,
                            edge=edge if plan["boundary"] else None,
                            source=source,
                            chunk=chunk,
                            budget=budget,
                        )
                    ]
    if split.exists():
        children = record(split)["children"]
    else:
        try:
            return [acquire(chunk, budget, plan, **options)]
        except (RequestTooLarge, TransferTooLarge) as error:
            if chunk.get("boundary"):
                raise
            children = split_chunk(chunk)
            reason = type(error).__name__
            secondary(
                error,
                lambda: budget.write_json(
                    split,
                    signed(
                        dict(
                            version=1,
                            request=selection,
                            children=children,
                            status="EXPLICIT_SIZE_REJECTION_SPLIT",
                            reason=reason,
                        )
                    ),
                ),
            )
            if not split.exists():
                raise
            state = record(directory / "ATTEMPT.json")
            budget.finish_rejected_download(directory / state["attempt"] / "source.grib.partial")
            budget.retain_directory(directory)
            # 413 is an explicit rejection, not an ambiguous accepted submission.
            # Preserve the rejected parent; children have different exact identities.
    result = []
    for child in children:
        result.extend(acquire_adaptive(child, budget, plan, output=output, **options))
    return result


OUTPUT_TYPES = {
    "DATE": pa.string(),
    "TIMEZONE": pa.string(),
    "H3_INDEX": pa.string(),
    "METRIC": pa.string(),
    "VALUE": pa.float64(),
    "UNITS": pa.string(),
    "SOURCE": pa.string(),
    "BACKEND": pa.string(),
    "STATUS": pa.string(),
    "VALID_HOURS": pa.int64(),
    "INVALID_HOURS": pa.int64(),
    "INVALID_DERIVATION_BITS": pa.string(),
    "EXPECTED_HOURS": pa.int64(),
    "COVERAGE_FRACTION": pa.float64(),
    "INTERVAL_START_UTC": pa.string(),
    "INTERVAL_END_UTC": pa.string(),
    "VALID_TIME_UTC": pa.string(),
    "SOURCE_GRID_HASH": pa.string(),
    "SOURCE_GRID_INDEX": pa.int64(),
    "SOURCE_LAT": pa.float64(),
    "SOURCE_LON": pa.float64(),
    "SOURCE_GRID_DISTANCE_M": pa.float64(),
    "SPATIAL_METHOD": pa.string(),
    "SOURCE_LINEAGE_ID": pa.string(),
    "INPUT_MISSING_BITS": pa.string(),
    "VALUE_STATUS": pa.string(),
    "CONSOLIDATION_STATUS": pa.string(),
    "SOURCE_RESOLUTION": pa.string(),
    "WIND_VECTOR_BASIS": pa.string(),
    "AVAILABLE_AT_UTC": pa.string(),
    "METHOD": pa.string(),
    "SCHEMA": pa.string(),
}
OUTPUT_SCHEMA = pa.schema([pa.field(name, kind) for name, kind in OUTPUT_TYPES.items()])


def valid_partition(value, root):
    if "partition_manifest" in value:
        receipt = root / value["partition_manifest"]["path"]
        if not receipt.is_file() or checksum_path(receipt) != value["partition_manifest"]["sha256"]:
            return False
        captured = record(receipt)
        if captured["artifact"] != value["artifact"] or captured["sources"] != value["sources"]:
            return False
    path = root / value["artifact"]["path"]
    if not path.is_relative_to(root) or ".." in Path(value["artifact"]["path"]).parts:
        raise ValueError("Partition path escapes output root.")
    if not path.is_file() or checksum_path(path) != value["artifact"]["sha256"]:
        return False
    file = pq.ParquetFile(path)
    if file.metadata.num_rows != value["artifact"]["rows"] or not file.schema_arrow.equals(
        OUTPUT_SCHEMA, check_metadata=False
    ):
        return False
    for source in value["sources"]:
        try:
            validate_source(source)
        except ValueError:
            return False
    return True


def process(item, following, plan, output, budget):
    started = time.perf_counter()
    request, chunk = plan["request"], item["chunk"]
    partition_key = digest(dict(request=request, start=chunk["start"], end=chunk["end"]))
    directory = output / "partitions" / chunk["start"][:7] / partition_key
    pointer = directory / "CURRENT.json"
    if pointer.exists():
        with lease(directory / ".publish.lock"):
            previous = record(pointer)
            if valid_partition(previous, output):
                confirm_pointer(pointer)
                return previous
    cube = read_cube(item["cube"])
    boundary = read_cube(following["edge"]) if following and following["edge"] else None
    mapping_started = time.perf_counter()
    spatial = mapping(
        cube,
        plan["request"]["bbox"],
        plan["request"]["spatial_output"],
        budget.namespace / "mappings",
    )
    mapping_seconds = time.perf_counter() - mapping_started
    directory.mkdir(parents=True, exist_ok=True)
    with lease(directory / ".publish.lock"):
        pointer = directory / "CURRENT.json"
        if pointer.exists():
            prior = record(pointer)
            if valid_partition(prior, output):
                confirm_pointer(pointer)
                return prior
        token = uuid.uuid4().hex
        stage = budget.run_root / "scratch" / (token + ".parquet")
        stage.parent.mkdir(parents=True, exist_ok=True)
        lineage, rows, statuses, complete = {}, 0, set(), True
        writer = pq.ParquetWriter(stage, OUTPUT_SCHEMA, compression="zstd")
        budget.begin_write(stage, 65536)
        aggregation_seconds, write_seconds = 0.0, 0.0
        try:
            first, last = date.fromisoformat(chunk["start"]), date.fromisoformat(chunk["end"])
            day = first
            while day <= last:
                iterator = iter(
                    day_frames(cube, boundary, day.isoformat(), request, spatial, lineage)
                )
                while True:
                    begin = time.perf_counter()
                    try:
                        frame = next(iterator)
                    except StopIteration:
                        break
                    aggregation_seconds += time.perf_counter() - begin
                    budget.checkpoint(
                        additional_memory=int(frame.memory_usage(deep=True).sum()) * 2
                    )
                    table = pa.Table.from_pandas(frame, schema=OUTPUT_SCHEMA, preserve_index=False)
                    budget.reserve_more(stage, table.nbytes + 65536)
                    begin = time.perf_counter()
                    writer.write_table(table, row_group_size=4096)
                    write_seconds += time.perf_counter() - begin
                    rows += len(frame)
                    statuses.update(frame.STATUS)
                    complete &= bool((frame.VALID_HOURS == frame.EXPECTED_HOURS).all())
                day += timedelta(days=1)
        finally:
            writer.close()
            budget.finish_write(stage)
        sync_file(stage)
        target = directory / (token + ".parquet")
        if shutil.disk_usage(output).free < stage.stat().st_size + 16 * 1024**2:
            raise LimitExceeded("Insufficient physical output disk space.")
        budget.check_disk(budget.namespace, additional_bytes=stage.stat().st_size)
        try:
            os.link(stage, target)
        except OSError:
            with stage.open("rb") as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, 1024**2)
        sync_file(target)
        sources = [item["source"]]
        if following and set(request["parameters"]) & {"tp", "sf"}:
            sources.append(following["source"])
        value = signed(
            dict(
                version=1,
                request=request,
                start=chunk["start"],
                end=chunk["end"],
                artifact=dict(
                    path=str(target.relative_to(output)),
                    bytes=target.stat().st_size,
                    sha256=checksum_path(target),
                    rows=rows,
                ),
                sources=sources,
                lineage=lineage,
                status="COMPLETE" if complete else "PARTIAL",
                timings=dict(
                    mapping_seconds=mapping_seconds,
                    aggregation_seconds=aggregation_seconds,
                    parquet_write_seconds=write_seconds,
                    total_seconds=time.perf_counter() - started,
                ),
            )
        )
        budget.resources.metric("spatial_mapping_seconds", mapping_seconds)
        budget.resources.metric("aggregation_seconds", aggregation_seconds)
        budget.resources.metric("parquet_write_seconds", write_seconds)
        if not valid_partition(value, output):
            raise ValueError("Candidate backfill partition failed validation.")
        receipt = directory / (token + ".json")
        atomic_json(receipt, value)
        summary = signed(
            dict(
                {k: v for k, v in value.items() if k not in {"lineage", "sha256"}},
                partition_manifest=dict(
                    path=str(receipt.relative_to(output)), sha256=checksum_path(receipt)
                ),
            )
        )
        atomic_json(pointer, summary)
        # Newly created, explicitly ephemeral scratch only. Existing cached source
        # objects and prior immutable result attempts are never removed.
        budget.discard_scratch(stage)
        return summary


class BackfillResult:
    def __init__(self, directory, manifest, *, cache_hit=False, warnings=None, execution=None):
        self.directory, self.manifest = Path(directory), manifest
        self.cache_hit = cache_hit
        self.operational_warnings = warnings or []
        self.execution = execution or dict(provider_requests=0, transferred_bytes=0, decodes=0)

    @property
    def metadata(self):
        return dict(
            self.manifest,
            cache_hit=self.cache_hit,
            operational_warnings=self.operational_warnings,
            execution=self.execution,
            publication_status="COMMITTED_WITH_OPERATIONAL_WARNING"
            if self.operational_warnings
            else "COMMITTED",
        )

    def iter_tables(self):
        for partition in self.manifest["partitions"]:
            if not valid_partition(partition, self.directory):
                raise ValueError(
                    "Backfill partition is corrupt; resume selective recovery before reading."
                )
            for batch in pq.ParquetFile(
                self.directory / partition["artifact"]["path"]
            ).iter_batches(batch_size=4096):
                yield pa.Table.from_batches([batch])

    def to_pandas(self, *, max_rows=1000000):
        if self.manifest["row_count"] > max_rows:
            raise ValueError(
                "Historical table exceeds materialization cap; use iter_tables or partitioned Parquet."
            )
        return pa.concat_tables(list(self.iter_tables())).to_pandas()

    def to_parquet(self, path):
        path = Path(path).resolve()
        if path.exists():
            raise FileExistsError("Retained export evidence is never overwritten.")
        path.parent.mkdir(parents=True, exist_ok=True)
        stage = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
        with pq.ParquetWriter(stage, OUTPUT_SCHEMA, compression="zstd") as writer:
            for table in self.iter_tables():
                writer.write_table(table)
        sync_file(stage)
        os.link(stage, path)  # Atomic publication without overwriting another exporter.
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        stage.unlink()
        return path


def backfill_weather(
    start_date=None,
    end_date=None,
    bbox=None,
    data_source="era5",
    *,
    variables=DEFAULT_VARIABLES,
    frequency="daily",
    spatial_output="native",
    cache_dir=None,
    output_dir=None,
    workers=2,
    resume=True,
    dry_run=False,
    study_config=None,
    backend="cds",
    memory_bytes=1024**3,
    staging_bytes=2 * 1024**3,
    source_chunk_bytes=64 * 1024**2,
    transfer_bytes=64 * 1024**3,
    max_requests=20000,
    max_seconds=48 * 3600,
    retained_cache_bytes=None,
    retain_intermediates=False,
    retries=3,
    credential_env_file=None,
    progress=None,
    _provider_factory=None,
):
    """Acquire arbitrary retrospective ERA5 ranges with bounded monthly chunks.

    Dates are inclusive UTC. Native daily reduction precedes optional H3 R5
    sampling. `_provider_factory` is an internal offline-test seam, not a backend.
    """
    if data_source != "era5":
        raise ValueError(
            "Historical backfill currently uses ERA5; existing HRRR getters remain available."
        )
    if type(retries) is not int or not 0 <= retries <= 6:
        raise ValueError("Use 0–6 bounded transient retries.")
    if (
        type(max_requests) is not int
        or max_requests <= 0
        or type(transfer_bytes) is not int
        or transfer_bytes <= 0
        or not 0 < max_seconds <= 30 * 86400
        or (
            retained_cache_bytes is not None
            and (type(retained_cache_bytes) is not int or retained_cache_bytes <= 0)
        )
    ):
        raise ValueError("Positive backfill resource caps required; max runtime is 30 days.")
    plan = plan_backfill(
        variables,
        start_date,
        end_date,
        bbox,
        frequency=frequency,
        spatial_output=spatial_output,
        workers=workers,
        source_cap=source_chunk_bytes,
        memory_bytes=memory_bytes,
        staging_bytes=staging_bytes,
        backend=backend,
        study_config=study_config,
    )
    if dry_run:
        plan["access"] = resolve_credentials(
            env_file=credential_env_file, inspect_only=True
        ).public()
        plan["aggregate_limits"] = dict(
            requests=max_requests, transfer_bytes=transfer_bytes, seconds=max_seconds
        )
        return plan
    study = plan["request"]["study_identity"]
    if study is not None:
        data_root = Path(study["resolved_data_root"])
        cache_dir = cache_dir or data_root / "meteorology/raw/era5-backfill"
        output_dir = output_dir or data_root / "meteorology/native/era5-backfill"
    cache = Path(cache_dir or "./weather-cache").expanduser().resolve() / "backfill-cds-v1"
    output = Path(output_dir or "./meteorology-backfill").expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    run = cache / "runs" / plan["run_id"]
    with lease(run / ".run.lock"):
        active = output / ("BACKFILL-" + plan["run_id"] + ".json")
        if active.exists():
            value = record(active)
            if all(valid_partition(p, output) for p in value["partitions"]):
                if not resume:
                    raise FileExistsError(
                        "Existing backfill evidence requires resume=True or a different output directory."
                    )
                sync_file(active)
                descriptor = os.open(output, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                return BackfillResult(output, value, cache_hit=True)
        resources = RunResources(
            run,
            requests=max_requests,
            transfer_bytes=transfer_bytes,
            seconds=max_seconds,
            memory_bytes=memory_bytes,
            workers=workers,
        )
        settings = dict(
            source_cap=source_chunk_bytes,
            staging_bytes=staging_bytes,
            retained_cache_bytes=retained_cache_bytes,
        )
        if (run / "SETTINGS.json").exists() and record(run / "SETTINGS.json")[
            "settings"
        ] != settings:
            raise ValueError("Resume requires original chunk/storage resource limits.")
        atomic_json(run / "SETTINGS.json", signed(dict(settings=settings)))
        budgets = [
            BackfillBudget(
                resources, cache, run / f"worker-{i}", staging_bytes, retained_cache_bytes
            )
            for i in range(workers + 1)
        ]
        chunks = plan["chunks"] + ([plan["boundary"]] if plan["boundary"] else [])
        partitions, futures = [], {}
        completed = {}
        for index, chunk in enumerate(plan["chunks"]):
            key = digest(dict(request=plan["request"], start=chunk["start"], end=chunk["end"]))
            pointer = output / "partitions" / chunk["start"][:7] / key / "CURRENT.json"
            if pointer.exists():
                previous = record(pointer)
                if valid_partition(previous, output):
                    confirm_pointer(pointer)
                    completed[index] = previous
        started = time.perf_counter()
        with charge_failures(resources), ThreadPoolExecutor(max_workers=workers) as pool:

            def submit(index):
                from concurrent.futures import Future

                existing = completed.get(index)
                if index == len(plan["chunks"]) and index - 1 in completed:
                    existing = completed[index - 1]
                if existing is not None:
                    source = (
                        existing["sources"][-1]
                        if index == len(plan["chunks"])
                        else existing["sources"][0]
                    )
                    edge = (
                        Path(source["path"]).parents[2]
                        / "edges"
                        / (source["sha256"] + "-" + chunks[index]["start"] + ".npz")
                    )
                    if not plan["boundary"] or edge.exists():
                        future = Future()
                        future.set_result(
                            dict(
                                cube=None,
                                edge=edge if plan["boundary"] else None,
                                source=source,
                                chunk=chunks[index],
                                budget=budgets[index % workers],
                            )
                        )
                        futures[index] = future
                        return
                futures[index] = pool.submit(
                    acquire_adaptive,
                    chunks[index],
                    budgets[index % workers],
                    plan,
                    credential_env_file=credential_env_file,
                    retries=retries,
                    provider_factory=_provider_factory,
                    output=output,
                )

            for index in range(min(len(chunks), workers + 1)):
                submit(index)
            for index in range(len(plan["chunks"])):
                items = futures[index].result()
                if isinstance(items, dict):
                    items = [items]
                following_items = (
                    futures[index + 1].result() if plan["boundary"] is not None else None
                )
                if isinstance(following_items, dict):
                    following_items = [following_items]
                for offset, item in enumerate(items):
                    following = (
                        items[offset + 1]
                        if offset + 1 < len(items)
                        else following_items[0]
                        if following_items
                        else None
                    )
                    partition = (
                        completed[index]
                        if index in completed
                        else process(item, following, plan, output, budgets[-1])
                    )
                    partitions.append(partition)
                    if not retain_intermediates and item["cube"] is not None:
                        item["budget"].discard_scratch(item["cube"])
                        item["budget"].discard_scratch(item["cube"].with_suffix(".json"))
                atomic_json(
                    run / "PROGRESS.json",
                    signed(
                        dict(
                            version=1,
                            run_id=plan["run_id"],
                            completed=[
                                dict(start=p["start"], end=p["end"], artifact=p["artifact"])
                                for p in partitions
                            ],
                        )
                    ),
                )
                pending = index + workers + 1
                if pending < len(chunks):
                    submit(pending)
                if progress is not None:
                    progress(
                        dict(
                            completed_chunks=len(partitions),
                            total_chunks=len(plan["chunks"]),
                            completed_days=sum(
                                (date.fromisoformat(p["end"]) - date.fromisoformat(p["start"])).days
                                + 1
                                for p in partitions
                            ),
                            total_days=(
                                date.fromisoformat(plan["request"]["end"])
                                - date.fromisoformat(plan["request"]["start"])
                            ).days
                            + 1,
                            elapsed_seconds=time.perf_counter() - started,
                        )
                    )
        if plan["boundary"] and not retain_intermediates:
            final = futures[len(chunks) - 1].result()
            if isinstance(final, list):
                final = final[0]
            if final["cube"] is not None:
                final["budget"].discard_scratch(final["cube"])
                final["budget"].discard_scratch(final["cube"].with_suffix(".json"))
        value = signed(
            dict(
                version=1,
                request=plan["request"],
                run_id=plan["run_id"],
                method=METHOD,
                schema=SCHEMA,
                partitions=partitions,
                row_count=sum(p["artifact"]["rows"] for p in partitions),
                status="COMPLETE"
                if all(p["status"] == "COMPLETE" for p in partitions)
                else "PARTIAL",
                budget=resources.receipt(),
                total_seconds=time.perf_counter() - started,
                source_resolution=plan["source_resolution"],
                production_shared_study_release=False,
                qualification=dict(
                    independent_regional_accuracy="NOT_RUN", backend_equivalence="NOT_RUN"
                ),
            )
        )
        identity = digest(
            dict(
                request=plan["request"],
                artifacts=[p["artifact"]["sha256"] for p in partitions],
                sources=[
                    [(s["sha256"], s["retrieved_at_utc"]) for s in p["sources"]] for p in partitions
                ],
            )
        )
        value = signed(dict(value, result_id=identity))
        manifests = output / "manifests"
        manifests.mkdir(exist_ok=True)
        manifest_path = manifests / (identity + "-" + uuid.uuid4().hex + ".json")
        atomic_json(manifest_path, value)
        execution = resources.execution()
        committed = False
        replaced = False

        def on_replace():
            nonlocal replaced
            replaced = True

        def on_durable():
            nonlocal committed
            committed = True

        try:
            with lease(output / ".publish.lock"):
                atomic_json(active, value, on_replace=on_replace, on_durable=on_durable)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            if committed:
                return BackfillResult(
                    output,
                    value,
                    warnings=[dict(stage="post_commit", error_type=type(error).__name__)],
                    execution=execution,
                )
            if replaced:
                raise PublicationUncertainError(
                    "Backfill pointer replacement observed; resume to confirm durability."
                ) from error
            raise
        return BackfillResult(output, value, execution=execution)
