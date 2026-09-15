"""Acquire strict direct-HRRR analyses and retain compact R5 samples."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from meteorology.core.artifacts import TransactionalFamilyPublisher, atomic_write_json
from meteorology.core.data.meteorological_schemas import HRRR_INVENTORY_SCHEMA as INVENTORY_SCHEMA
from meteorology.core.data.meteorological_schemas import (
    PRE_F01_INVENTORY_SCHEMA,
)

from ..artifacts import (
    checksum_path,
    manifest_payload,
    parquet_contract,
    sha256_file,
    write_manifest,
    write_table,
)
from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from ..spatial_support.build import load_meteorological_support
from .sampling import (
    CROSSWALK_SCHEMA,
    PRE_F01_SAMPLE_SCHEMA,
    SAMPLE_SCHEMA,
    build_nearest_grid_crosswalk,
    make_sample_times_for_local_date,
    replace_sample_precipitation,
    sample_source_grid,
)
from .source import (
    FORECAST_HOUR,
    HRRR_MODEL,
    HRRR_PRODUCT,
    HRRR_VARIABLES,
    fetch_cropped_hrrr_grid,
    fetch_cropped_hrrr_precip_grid,
    hrrr_aws_archive_uri,
    replace_source_grid_precipitation,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_HERBIE_WORKERS = 4
MAX_HERBIE_WORKERS = 16
HERBIE_ATTEMPTS_PER_TIMESTAMP = 3


def sample_path(raw_dir: Path, valid_time_utc: pd.Timestamp, timezone: str) -> Path:
    valid = pd.Timestamp(valid_time_utc)
    valid = valid.tz_localize("UTC") if valid.tzinfo is None else valid.tz_convert("UTC")
    local_date = valid.tz_convert(timezone).strftime("%Y-%m-%d")
    stamp = valid.strftime("%Y%m%dT%H%M%SZ")
    return (
        raw_dir / "samples" / f"year={local_date[:4]}" / f"date={local_date}" / f"{stamp}.parquet"
    )


def crosswalk_path(raw_dir: Path, source_grid_hash: str) -> Path:
    return raw_dir / "crosswalks" / f"source_grid_hash={source_grid_hash}" / "part-000.parquet"


def _expected_times(
    start_date: str, end_date: str, timezone: str, interval_hours: int
) -> list[pd.Timestamp]:
    times: list[pd.Timestamp] = []
    for value in pd.date_range(start_date, end_date, freq="D"):
        times.extend(
            make_sample_times_for_local_date(value.strftime("%Y-%m-%d"), timezone, interval_hours)
        )
    return times


def _load_inventory(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    parquet = pq.ParquetFile(path)
    schema = parquet.schema_arrow
    if not schema.equals(INVENTORY_SCHEMA, check_metadata=False) and not schema.equals(
        PRE_F01_INVENTORY_SCHEMA, check_metadata=False
    ):
        raise ValueError(f"R5 HRRR inventory has an incompatible schema: {path}")
    rows = parquet.read().to_pylist()
    for row in rows:
        row.setdefault("PRECIP_INIT_TIME_UTC", None)
        row.setdefault("PRECIP_FORECAST_HOUR", None)
        row.setdefault("PRECIP_SOURCE_URI", None)
    valid_times = [str(row["VALID_TIME_UTC"]) for row in rows]
    if len(valid_times) != len(set(valid_times)):
        raise ValueError(f"R5 HRRR inventory contains duplicate valid times: {path}")
    return rows


def _valid_crosswalk(path: Path, support: pd.DataFrame, expected_hash: str | None = None) -> bool:
    if not path.exists():
        return False
    try:
        parquet = pq.ParquetFile(path)
        if not parquet.schema_arrow.equals(CROSSWALK_SCHEMA, check_metadata=False):
            return False
        frame = parquet.read().to_pandas()
    except Exception:
        return False
    return (
        len(frame) == len(support)
        and not frame["H3_INDEX"].duplicated().any()
        and set(frame["H3_INDEX"].astype(str)) == set(support["H3_INDEX"].astype(str))
        and (expected_hash is None or set(frame["SOURCE_GRID_HASH"].astype(str)) == {expected_hash})
        and np.isfinite(frame["SOURCE_GRID_DISTANCE_M"].to_numpy(dtype=float)).all()
    )


def _valid_sample(
    path: Path,
    support: pd.DataFrame,
    *,
    valid_time_utc: pd.Timestamp | None = None,
) -> bool:
    if not path.exists():
        return False
    try:
        parquet = pq.ParquetFile(path)
        if not parquet.schema_arrow.equals(SAMPLE_SCHEMA, check_metadata=False):
            return False
        frame = parquet.read().to_pandas()
    except Exception:
        return False
    if len(frame) != len(support) or frame["H3_INDEX"].duplicated().any():
        return False
    if set(frame["H3_INDEX"].astype(str)) != set(support["H3_INDEX"].astype(str)):
        return False
    if set(frame["SOURCE_DATA_STATE"].astype(str)) != {"COMPLETE"}:
        return False
    if valid_time_utc is not None:
        valid = pd.Timestamp(valid_time_utc)
        valid = valid.tz_localize("UTC") if valid.tzinfo is None else valid.tz_convert("UTC")
        if set(frame["VALID_TIME_UTC"].astype(str)) != {valid.isoformat()}:
            return False
        if set(frame["PRECIP_VALID_TIME_UTC"].astype(str)) != {valid.isoformat()}:
            return False
        if set(frame["PRECIP_FORECAST_HOUR"].astype(int)) != {1}:
            return False
        precip_init = valid - pd.Timedelta(hours=1)
        if set(frame["PRECIP_INIT_TIME_UTC"].astype(str)) != {precip_init.isoformat()}:
            return False
    numeric = frame.select_dtypes(include=[np.number]).to_numpy(dtype=float)
    return not frame.isna().any().any() and np.isfinite(numeric).all()


def _reusable_core_sample(
    path: Path,
    support: pd.DataFrame,
    *,
    valid_time_utc: pd.Timestamp,
) -> pd.DataFrame | None:
    """Return a checksum-independent cached f00 core sample suitable for precip repair."""

    if not path.exists():
        return None
    try:
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
        if not schema.equals(PRE_F01_SAMPLE_SCHEMA, check_metadata=False) and not schema.equals(
            SAMPLE_SCHEMA, check_metadata=False
        ):
            return None
        frame = parquet.read().to_pandas()
    except Exception:
        return None
    if (
        len(frame) != len(support)
        or frame["H3_INDEX"].duplicated().any()
        or set(frame["H3_INDEX"].astype(str)) != set(support["H3_INDEX"].astype(str))
        or set(frame["SOURCE_DATA_STATE"].astype(str)) != {"COMPLETE"}
    ):
        return None
    valid = pd.Timestamp(valid_time_utc).tz_convert("UTC").isoformat()
    if set(frame["VALID_TIME_UTC"].astype(str)) != {valid}:
        return None
    core_numeric = [
        column
        for column in PRE_F01_SAMPLE_SCHEMA.names
        if pa.types.is_floating(PRE_F01_SAMPLE_SCHEMA.field(column).type)
        or pa.types.is_integer(PRE_F01_SAMPLE_SCHEMA.field(column).type)
    ]
    values = frame[core_numeric].to_numpy(dtype=float)
    return frame if not frame.isna().any().any() and np.isfinite(values).all() else None


def _atomic_table_write(frame: pd.DataFrame, destination: Path, schema: pa.Schema) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    try:
        write_table(temporary, pa.Table.from_pandas(frame, preserve_index=False), schema)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _inventory_row(
    *,
    valid_time: pd.Timestamp,
    timezone: str,
    availability_lag_hours: int,
    h3_resolution: int,
    h3_cell_count: int,
    raw_dir: Path,
    sample: Path | None,
    crosswalk: Path | None,
    status: str,
    source_uri: str | None = None,
    precip_source_uri: str | None = None,
    failure_reason: str | None = None,
    sample_checksum: str | None = None,
    crosswalk_checksum: str | None = None,
    source_grid_hash: str | None = None,
) -> dict[str, object]:
    valid = pd.Timestamp(valid_time)
    valid = valid.tz_localize("UTC") if valid.tzinfo is None else valid.tz_convert("UTC")
    init = valid - pd.Timedelta(hours=FORECAST_HOUR)
    precip_forecast_hour = 1
    precip_init = valid - pd.Timedelta(hours=precip_forecast_hour)
    available = valid + pd.Timedelta(hours=availability_lag_hours)
    sample_frame: pd.DataFrame | None = None
    if source_grid_hash is None and sample is not None and sample.exists():
        sample_frame = pq.read_table(sample, columns=["SOURCE_GRID_HASH"]).to_pandas()
    return {
        "LOCAL_DATE": valid.tz_convert(timezone).strftime("%Y-%m-%d"),
        "VALID_TIME_UTC": valid.isoformat(),
        "INIT_TIME_UTC": init.isoformat(),
        "AVAILABLE_AT_UTC": available.isoformat(),
        "SOURCE_URI": source_uri,
        "RELATIVE_PATH": str(sample.relative_to(raw_dir)) if sample is not None else None,
        "CHECKSUM": sample_checksum
        or (sha256_file(sample) if sample is not None and sample.exists() else None),
        "CROSSWALK_RELATIVE_PATH": (
            str(crosswalk.relative_to(raw_dir)) if crosswalk is not None else None
        ),
        "CROSSWALK_CHECKSUM": crosswalk_checksum
        or (sha256_file(crosswalk) if crosswalk is not None and crosswalk.exists() else None),
        "SOURCE_GRID_HASH": (
            source_grid_hash
            or (
                str(sample_frame["SOURCE_GRID_HASH"].iloc[0])
                if sample_frame is not None and not sample_frame.empty
                else None
            )
        ),
        "H3_RESOLUTION": int(h3_resolution),
        "H3_CELL_COUNT": int(h3_cell_count),
        "VARIABLE_COUNT": len(HRRR_VARIABLES) if status == "COMPLETE" else 0,
        "STATUS": status,
        "FAILURE_REASON": failure_reason,
        "SOURCE_BACKEND": "grib",
        "SOURCE_FORMAT": "GRIB2",
        "PRECISION_QC_STATE": "NATIVE_GRIB_DECODING",
        "PRECIP_INIT_TIME_UTC": precip_init.isoformat() if status == "COMPLETE" else None,
        "PRECIP_FORECAST_HOUR": precip_forecast_hour if status == "COMPLETE" else None,
        "PRECIP_SOURCE_URI": precip_source_uri if status == "COMPLETE" else None,
    }


def _publish_complete_acquisition(
    *,
    config_path: Path,
    config: Any,
    rows: list[dict[str, Any]],
    inventory_destination: Path,
    manifest_destination: Path,
    start: str,
    end: str,
    run_id: str,
) -> None:
    weather = config.surface_weather
    for row in rows:
        valid_time = pd.Timestamp(str(row["VALID_TIME_UTC"]))
        expected_uri = hrrr_aws_archive_uri(valid_time)
        expected_precip_uri = hrrr_aws_archive_uri(valid_time, weather.precipitation_forecast_hour)
        if (
            row.get("STATUS") != "COMPLETE"
            or row.get("SOURCE_URI") != expected_uri
            or row.get("PRECIP_SOURCE_URI") != expected_precip_uri
            or int(row.get("PRECIP_FORECAST_HOUR") or -1) != weather.precipitation_forecast_hour
        ):
            raise ValueError(
                "Canonical HRRR acquisition publication requires COMPLETE f00 core and f01 "
                f"precipitation provenance for {valid_time.isoformat()}."
            )
    inventory_table = pa.Table.from_pylist(rows, schema=INVENTORY_SCHEMA)
    publication_parent = Path(
        os.path.commonpath([inventory_destination.parent, manifest_destination.parent])
    )
    with TransactionalFamilyPublisher(publication_parent, run_id=run_id) as publisher:
        staged_inventory = publisher.stage_path(inventory_destination)
        staged_manifest = publisher.stage_manifest_path(manifest_destination)
        write_table(staged_inventory, inventory_table, INVENTORY_SCHEMA)
        inventory_contract = parquet_contract(
            staged_inventory, published_path=inventory_destination
        )
        payload = manifest_payload(
            product="meteorological.surface_weather.download",
            run_id=run_id,
            config_path=config_path,
            resolved_config={
                "start_date": start,
                "end_date": end,
                "timezone": weather.timezone,
                "interval_hours": weather.interval_hours,
                "h3_resolution": weather.h3_resolution,
                "availability_lag_hours": weather.availability_lag_hours,
                "source": {
                    "model": HRRR_MODEL,
                    "product": HRRR_PRODUCT,
                    "forecast_hour": FORECAST_HOUR,
                    "precipitation_forecast_hour": weather.precipitation_forecast_hour,
                    "backend": "direct_grib_via_herbie",
                    "variable_selectors": HRRR_VARIABLES,
                },
                "sample_storage": "one strict H3 R5 Parquet file per valid time",
            },
            artifacts=[inventory_contract],
            inputs=[
                {
                    "path": str(config.support_path(weather.h3_resolution)),
                    "checksum": checksum_path(config.support_path(weather.h3_resolution)),
                },
                {
                    "path": str(config.support_manifest_path),
                    "checksum": checksum_path(config.support_manifest_path),
                },
            ],
            sources=[
                {
                    "name": "NOAA High-Resolution Rapid Refresh surface analysis",
                    "model": HRRR_MODEL,
                    "product": HRRR_PRODUCT,
                    "forecast_hour": FORECAST_HOUR,
                    "retrieval_client": "Herbie",
                    "license": "United States government data; consult NOAA source terms",
                    "attribution": "NOAA/NCEP HRRR",
                    "observation_period": f"{start} through {end}",
                    "redistribution_restrictions": "Consult authoritative NOAA archive terms",
                },
                {
                    "name": "NOAA High-Resolution Rapid Refresh one-hour precipitation forecast",
                    "model": HRRR_MODEL,
                    "product": HRRR_PRODUCT,
                    "forecast_hour": weather.precipitation_forecast_hour,
                    "variable_selector": HRRR_VARIABLES["precip_rate_kg_m2_s"],
                    "retrieval_client": "Herbie",
                    "license": "United States government data; consult NOAA source terms",
                    "attribution": "NOAA/NCEP HRRR",
                    "observation_period": f"{start} through {end}",
                    "redistribution_restrictions": "Consult authoritative NOAA archive terms",
                },
            ],
            h3_resolution=weather.h3_resolution,
            spatial_bounds=config.bbox,
            temporal_coverage={"start_date": start, "end_date": end},
            source_completeness="complete",
            availability_semantics={
                "product_type": "retrospective_analysis_with_short_forecast_precipitation",
                "rule": "AVAILABLE_AT_UTC equals VALID_TIME_UTC plus the configured source lag.",
                "publication": "The canonical inventory is published only when every expected valid time is complete.",
            },
            limitations=[
                "The acquisition retains nearest-neighbour H3 samples and provenance, not complete HRRR GRIB files or cropped source grids.",
                "Every inventory row checksum-addresses its sample and crosswalk; the build verifies both before use.",
                "No missing, ambiguous, non-finite, or unsupported HRRR field is replaced with zero.",
                "Core weather uses f00 analyses; precipitation uses f01 PRATE initialized one hour earlier and valid at the same timestamp.",
            ],
        )
        write_manifest(staged_manifest, payload)
        publisher.publish()


def download_surface_weather(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    overwrite: bool = False,
    max_workers: int = DEFAULT_HERBIE_WORKERS,
    dry_run: bool = False,
    run_id: str | None = None,
    working_inventory_path: str | Path | None = None,
    inventory_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, object]:
    """Acquire a frozen direct-HRRR range and publish only when it is complete."""

    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    start = str(start_date or weather.start_date)
    freeze_path = weather.raw_dir / "R5_BACKFILL_FREEZE.json"
    use_latest_freeze = end_date is None and weather.end_date == "latest_complete"
    if use_latest_freeze and freeze_path.exists():
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        if str(freeze.get("start_date")) != start:
            raise ValueError(
                "Existing HRRR backfill freeze does not match the requested start date."
            )
        end = str(freeze["end_date"])
    else:
        end = str(end_date or weather.resolved_end_date())
    if pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError("Surface-weather download start_date must not follow end_date.")
    workers = int(max_workers)
    if not 1 <= workers <= MAX_HERBIE_WORKERS:
        raise ValueError(f"Direct-HRRR workers must be in [1, {MAX_HERBIE_WORKERS}].")
    times = _expected_times(start, end, weather.timezone, weather.interval_hours)
    summary: dict[str, object] = {
        "start_date": start,
        "end_date": end,
        "expected_times": len(times),
        "complete_times": 0,
        "failed_times": 0,
        "h3_resolution": weather.h3_resolution,
        "backend": "direct_grib_via_herbie",
        "workers": workers,
        "raw_dir": str(weather.raw_dir),
    }
    if dry_run:
        return summary
    if use_latest_freeze and not freeze_path.exists():
        atomic_write_json(
            freeze_path,
            {
                "schema_version": 1,
                "start_date": start,
                "end_date": end,
                "rule": "latest_complete resolved once at acquisition start and retained until complete",
            },
            overwrite=False,
        )

    support = load_meteorological_support(weather.h3_resolution, config_path)
    working_destination = Path(working_inventory_path or weather.working_inventory_path)
    inventory_destination = Path(inventory_path or weather.inventory_path)
    manifest_destination = Path(manifest_path or weather.acquisition_manifest_path)
    seed_path = working_destination if working_destination.exists() else inventory_destination
    existing_rows = _load_inventory(seed_path)
    existing_by_time = {str(row["VALID_TIME_UTC"]): row for row in existing_rows}
    crosswalk_cache: dict[str, pd.DataFrame] = {}
    crosswalk_validation_cache: dict[tuple[str, str], bool] = {}
    crosswalk_lock = Lock()

    def acquire(valid_time: pd.Timestamp) -> dict[str, object]:
        valid = pd.Timestamp(valid_time).tz_convert("UTC")
        valid_key = valid.isoformat()
        local_date = valid.tz_convert(weather.timezone).strftime("%Y-%m-%d")
        destination = sample_path(weather.raw_dir, valid, weather.timezone)
        existing = existing_by_time.get(valid_key, {})
        precip_forecast_hour = weather.precipitation_forecast_hour
        expected_precip_uri = hrrr_aws_archive_uri(valid, precip_forecast_hour)
        expected_checksum = str(existing.get("CHECKSUM") or "")
        expected_crosswalk_checksum = str(existing.get("CROSSWALK_CHECKSUM") or "")
        expected_hash = str(existing.get("SOURCE_GRID_HASH") or "") or None
        if expected_hash is None and _valid_sample(destination, support, valid_time_utc=valid):
            expected_hash = str(
                pq.read_table(destination, columns=["SOURCE_GRID_HASH"])[0][0].as_py()
            )
        existing_crosswalk = (
            weather.raw_dir / str(existing["CROSSWALK_RELATIVE_PATH"])
            if existing.get("CROSSWALK_RELATIVE_PATH")
            else (crosswalk_path(weather.raw_dir, expected_hash) if expected_hash else None)
        )
        actual_checksum = sha256_file(destination) if destination.exists() else ""
        checksum_ok = not expected_checksum or actual_checksum == expected_checksum
        actual_crosswalk_checksum = (
            sha256_file(existing_crosswalk)
            if existing_crosswalk is not None and existing_crosswalk.exists()
            else ""
        )
        crosswalk_checksum_ok = (
            not expected_crosswalk_checksum
            or actual_crosswalk_checksum == expected_crosswalk_checksum
        )
        validation_key = (str(existing_crosswalk), str(expected_hash))
        with crosswalk_lock:
            if validation_key not in crosswalk_validation_cache:
                crosswalk_validation_cache[validation_key] = (
                    existing_crosswalk is not None
                    and _valid_crosswalk(existing_crosswalk, support, expected_hash)
                )
            crosswalk_ok = crosswalk_validation_cache[validation_key] and crosswalk_checksum_ok
        precip_provenance_ok = (
            str(existing.get("PRECIP_SOURCE_URI") or "") == expected_precip_uri
            and int(existing.get("PRECIP_FORECAST_HOUR") or -1) == precip_forecast_hour
        )
        if (
            not overwrite
            and checksum_ok
            and _valid_sample(destination, support, valid_time_utc=valid)
            and crosswalk_ok
            and precip_provenance_ok
        ):
            source_uri = hrrr_aws_archive_uri(valid)
            return _inventory_row(
                valid_time=valid,
                timezone=weather.timezone,
                availability_lag_hours=weather.availability_lag_hours,
                h3_resolution=weather.h3_resolution,
                h3_cell_count=len(support),
                raw_dir=weather.raw_dir,
                sample=destination,
                crosswalk=existing_crosswalk,
                status="COMPLETE",
                source_uri=source_uri,
                precip_source_uri=expected_precip_uri,
                sample_checksum=actual_checksum,
                crosswalk_checksum=actual_crosswalk_checksum,
                source_grid_hash=expected_hash,
            )
        try:
            reusable_core = (
                _reusable_core_sample(destination, support, valid_time_utc=valid)
                if not overwrite and checksum_ok and crosswalk_ok
                else None
            )
            if reusable_core is not None and existing_crosswalk is not None:
                for attempt in range(1, HERBIE_ATTEMPTS_PER_TIMESTAMP + 1):
                    try:
                        precip_grid, precip_source_uri = fetch_cropped_hrrr_precip_grid(
                            valid_time_utc=valid,
                            bbox=config.bbox,
                            bbox_padding_degrees=weather.bbox_padding_degrees,
                            availability_lag_hours=weather.availability_lag_hours,
                            forecast_hour=precip_forecast_hour,
                        )
                        break
                    except Exception:
                        if attempt == HERBIE_ATTEMPTS_PER_TIMESTAMP:
                            raise
                        LOGGER.warning(
                            "HRRR f01 precipitation repair attempt %d/%d failed for %s; "
                            "retrying.",
                            attempt,
                            HERBIE_ATTEMPTS_PER_TIMESTAMP,
                            valid,
                        )
                        time.sleep(2 ** (attempt - 1))
                crosswalk = pq.read_table(existing_crosswalk, schema=CROSSWALK_SCHEMA).to_pandas()
                repaired = replace_sample_precipitation(
                    reusable_core,
                    precip_grid,
                    crosswalk,
                    valid_time_utc=valid,
                    forecast_hour=precip_forecast_hour,
                )
                _atomic_table_write(repaired, destination, SAMPLE_SCHEMA)
                if not _valid_sample(destination, support, valid_time_utc=valid):
                    raise RuntimeError(
                        f"Written HRRR precipitation repair failed validation: {destination}"
                    )
                return _inventory_row(
                    valid_time=valid,
                    timezone=weather.timezone,
                    availability_lag_hours=weather.availability_lag_hours,
                    h3_resolution=weather.h3_resolution,
                    h3_cell_count=len(support),
                    raw_dir=weather.raw_dir,
                    sample=destination,
                    crosswalk=existing_crosswalk,
                    status="COMPLETE",
                    source_uri=hrrr_aws_archive_uri(valid),
                    precip_source_uri=precip_source_uri,
                    source_grid_hash=expected_hash,
                )
            for attempt in range(1, HERBIE_ATTEMPTS_PER_TIMESTAMP + 1):
                try:
                    source_grid, source_uri = fetch_cropped_hrrr_grid(
                        valid_time_utc=valid,
                        bbox=config.bbox,
                        bbox_padding_degrees=weather.bbox_padding_degrees,
                        availability_lag_hours=weather.availability_lag_hours,
                    )
                    break
                except Exception:
                    if attempt == HERBIE_ATTEMPTS_PER_TIMESTAMP:
                        raise
                    LOGGER.warning(
                        "Direct HRRR fetch attempt %d/%d failed for %s; retrying.",
                        attempt,
                        HERBIE_ATTEMPTS_PER_TIMESTAMP,
                        valid,
                    )
                    time.sleep(2 ** (attempt - 1))
            for attempt in range(1, HERBIE_ATTEMPTS_PER_TIMESTAMP + 1):
                try:
                    precip_grid, precip_source_uri = fetch_cropped_hrrr_precip_grid(
                        valid_time_utc=valid,
                        bbox=config.bbox,
                        bbox_padding_degrees=weather.bbox_padding_degrees,
                        availability_lag_hours=weather.availability_lag_hours,
                        forecast_hour=precip_forecast_hour,
                    )
                    source_grid = replace_source_grid_precipitation(source_grid, precip_grid)
                    break
                except Exception:
                    if attempt == HERBIE_ATTEMPTS_PER_TIMESTAMP:
                        raise
                    LOGGER.warning(
                        "Direct HRRR f01 precipitation attempt %d/%d failed for %s; retrying.",
                        attempt,
                        HERBIE_ATTEMPTS_PER_TIMESTAMP,
                        valid,
                    )
                    time.sleep(2 ** (attempt - 1))
            grid_hash = str(source_grid["SOURCE_GRID_HASH"].iloc[0])
            crosswalk_destination = crosswalk_path(weather.raw_dir, grid_hash)
            with crosswalk_lock:
                crosswalk = crosswalk_cache.get(grid_hash)
                can_reuse_existing_crosswalk = expected_hash != grid_hash or crosswalk_checksum_ok
                if (
                    crosswalk is None
                    and can_reuse_existing_crosswalk
                    and _valid_crosswalk(crosswalk_destination, support, grid_hash)
                ):
                    crosswalk = pq.read_table(
                        crosswalk_destination, schema=CROSSWALK_SCHEMA
                    ).to_pandas()
                if crosswalk is None:
                    crosswalk = build_nearest_grid_crosswalk(support, source_grid)
                    _atomic_table_write(crosswalk, crosswalk_destination, CROSSWALK_SCHEMA)
                crosswalk_cache[grid_hash] = crosswalk
                crosswalk_validation_cache[(str(crosswalk_destination), grid_hash)] = True
            sampled = sample_source_grid(
                source_grid,
                crosswalk,
                local_date=local_date,
                valid_time_utc=valid,
                precip_init_time_utc=valid - pd.Timedelta(hours=precip_forecast_hour),
                precip_forecast_hour=precip_forecast_hour,
            )
            _atomic_table_write(sampled, destination, SAMPLE_SCHEMA)
            if not _valid_sample(destination, support, valid_time_utc=valid):
                raise RuntimeError(f"Written HRRR sample failed validation: {destination}")
            return _inventory_row(
                valid_time=valid,
                timezone=weather.timezone,
                availability_lag_hours=weather.availability_lag_hours,
                h3_resolution=weather.h3_resolution,
                h3_cell_count=len(support),
                raw_dir=weather.raw_dir,
                sample=destination,
                crosswalk=crosswalk_destination,
                status="COMPLETE",
                source_uri=source_uri,
                precip_source_uri=precip_source_uri,
                source_grid_hash=grid_hash,
            )
        except Exception as exc:
            LOGGER.exception("Direct HRRR acquisition failed for %s", valid)
            return _inventory_row(
                valid_time=valid,
                timezone=weather.timezone,
                availability_lag_hours=weather.availability_lag_hours,
                h3_resolution=weather.h3_resolution,
                h3_cell_count=len(support),
                raw_dir=weather.raw_dir,
                sample=None,
                crosswalk=None,
                status="FAILED",
                failure_reason=f"{type(exc).__name__}: {str(exc).splitlines()[0]}",
            )

    requested_rows: list[dict[str, object]] = []
    started = time.perf_counter()

    def checkpoint() -> list[dict[str, object]]:
        completed_keys = {str(row["VALID_TIME_UTC"]) for row in requested_rows}
        merged = [row for row in existing_rows if str(row["VALID_TIME_UTC"]) not in completed_keys]
        merged.extend(requested_rows)
        merged.sort(key=lambda row: str(row["VALID_TIME_UTC"]))
        _atomic_table_write(
            pa.Table.from_pylist(merged, schema=INVENTORY_SCHEMA).to_pandas(),
            working_destination,
            INVENTORY_SCHEMA,
        )
        return merged

    def record(row: dict[str, object]) -> None:
        requested_rows.append(row)
        if len(requested_rows) % 100 == 0:
            checkpoint()
            elapsed = max(time.perf_counter() - started, 1e-9)
            LOGGER.warning(
                "Direct HRRR R5 progress: %d/%d valid times validated (%.1f per minute, "
                "including cache hits)",
                len(requested_rows),
                len(times),
                len(requested_rows) / elapsed * 60.0,
            )

    if workers == 1:
        for value in times:
            record(acquire(value))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(acquire, value): value for value in times}
            for future in as_completed(futures):
                record(future.result())
    requested_rows.sort(key=lambda row: str(row["VALID_TIME_UTC"]))
    merged_rows = checkpoint()

    complete = sum(row["STATUS"] == "COMPLETE" for row in requested_rows)
    failed = len(requested_rows) - complete
    summary["complete_times"] = complete
    summary["failed_times"] = failed
    summary["working_inventory_path"] = str(working_destination)
    if failed or any(row["STATUS"] != "COMPLETE" for row in merged_rows):
        raise RuntimeError(
            f"Direct HRRR acquisition was incomplete: {failed} of {len(requested_rows)} "
            "requested valid times failed. Successful R5 samples and the working inventory "
            "were retained for checksum-based resumption; canonical acquisition artifacts "
            "were not replaced."
        )

    _publish_complete_acquisition(
        config_path=config.path,
        config=config,
        rows=merged_rows,
        inventory_destination=inventory_destination,
        manifest_destination=manifest_destination,
        start=min(str(row["LOCAL_DATE"]) for row in merged_rows),
        end=max(str(row["LOCAL_DATE"]) for row in merged_rows),
        run_id=run_id or f"hrrr-r5-download-{uuid.uuid4().hex[:12]}",
    )
    summary["inventory_path"] = str(inventory_destination)
    summary["manifest_path"] = str(manifest_destination)
    if use_latest_freeze:
        freeze_path.unlink(missing_ok=True)
    return summary


def snapshot_existing_surface_weather_acquisition(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    working_inventory_path: str | Path | None = None,
    inventory_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    """Publish a complete inventory from existing validated R5 timestamp samples."""

    config = load_meteorological_config(config_path)
    sample_files = sorted((config.surface_weather.raw_dir / "samples").rglob("*.parquet"))
    if not sample_files:
        raise FileNotFoundError(
            f"No existing R5 HRRR samples are available: {config.surface_weather.raw_dir / 'samples'}"
        )
    dates = sorted(
        {
            part.removeprefix("date=")
            for path in sample_files
            for part in path.parts
            if part.startswith("date=")
        }
    )
    expected_dates = [
        value.strftime("%Y-%m-%d") for value in pd.date_range(dates[0], dates[-1], freq="D")
    ]
    if dates != expected_dates:
        missing = sorted(set(expected_dates).difference(dates))
        raise ValueError(f"Existing R5 HRRR samples contain a date gap: {missing[0]}")
    result = download_surface_weather(
        config_path,
        start_date=dates[0],
        end_date=dates[-1],
        overwrite=False,
        max_workers=1,
        working_inventory_path=working_inventory_path,
        inventory_path=inventory_path,
        manifest_path=manifest_path,
        run_id=run_id,
    )
    result["snapshot_mode"] = "existing_validated_r5_samples"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--workers", type=int, default=DEFAULT_HERBIE_WORKERS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--logs", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.logs else logging.WARNING)
    if args.logs:
        os.environ["METEOROLOGY_WEATHER_LOGS"] = "1"
    print(
        download_surface_weather(
            args.config,
            start_date=args.start_date,
            end_date=args.end_date,
            overwrite=args.overwrite,
            max_workers=args.workers,
            dry_run=args.dry_run,
            run_id=args.run_id,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
