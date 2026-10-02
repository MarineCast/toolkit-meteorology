"""Acquire strict direct-HRRR analyses and retain compact R5 samples."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from meteorology.core.artifacts import TransactionalFamilyPublisher, atomic_write_json
from meteorology.core.data.meteorological_schemas import HRRR_INVENTORY_SCHEMA as INVENTORY_SCHEMA
from meteorology.core.data.meteorological_schemas import (
    LEGACY_HRRR_INVENTORY_SCHEMA,
    PRE_F01_INVENTORY_SCHEMA,
)

from ..artifacts import (
    checksum_path,
    load_manifest,
    manifest_payload,
    parquet_contract,
    sha256_file,
    write_manifest,
    write_table,
)
from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from ..field_contracts import FIELD_CONTRACT_VERSION, validate_fields
from ..methods import METHOD_VERSIONS
from ..spatial_support.build import load_meteorological_support
from .sampling import (
    AVAILABILITY_POLICY,
    CROSSWALK_SCHEMA,
    PRE_F01_SAMPLE_SCHEMA,
    SAMPLE_SCHEMA,
    SPATIAL_ACCEPTANCE_POLICY,
    build_nearest_grid_crosswalk,
    make_sample_times_for_local_date,
    replace_sample_precipitation,
    sample_source_grid,
    validate_crosswalk_distance,
)
from .source import (
    FORECAST_HOUR,
    HRRR_MODEL,
    HRRR_PRODUCT,
    HRRR_VARIABLES,
    fetch_cropped_hrrr_grid,
    fetch_cropped_hrrr_precip_grid,
    hrrr_logical_object_uri,
    replace_source_grid_precipitation,
)
from .storage import (
    acquisition_lock,
    copy_referenced_object,
    resolve_raw_relative,
    write_immutable_table,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_HERBIE_WORKERS = 4
MAX_HERBIE_WORKERS = 16
HERBIE_ATTEMPTS_PER_TIMESTAMP = 3
ACQUISITION_EVIDENCE_POLICY = "row-bound-current-source-v1"


def _row_evidence_key(row: dict[str, Any]) -> tuple:
    """Fields whose identity must survive an acquisition snapshot or cache reuse."""
    return tuple(row.get(field) for field in (
        "VALID_TIME_UTC", "RELATIVE_PATH", "CHECKSUM", "CROSSWALK_RELATIVE_PATH",
        "CROSSWALK_CHECKSUM", "SOURCE_GRID_HASH", "SOURCE_URI", "SOURCE_OBJECT_URI",
        "SOURCE_RETRIEVED_AT_UTC", "PRECIP_SOURCE_URI", "PRECIP_OBJECT_URI",
        "PRECIP_RETRIEVED_AT_UTC", "AVAILABLE_AT_UTC",
    ))


def _row_evidence_path(raw_dir: Path, key: tuple) -> Path:
    digest = hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()
    return raw_dir / "evidence" / f"{digest}.json"


def _core_evidence_key(row: dict[str, Any]) -> tuple:
    key = _row_evidence_key(row)
    return (*key[:9], key[12])


def _record_live_row_evidence(config: Any, row: dict[str, Any]) -> None:
    """Retain a row-bound result of the native-grid acceptance path for resumption."""
    weather = config.surface_weather
    key = _row_evidence_key(row)
    path = _row_evidence_path(weather.raw_dir, key)
    payload = {
        "row_identity": list(key),
        "method_version": METHOD_VERSIONS["meteorological.surface_weather.download"],
        "field_contract_version": FIELD_CONTRACT_VERSION,
        "spatial_acceptance_policy": SPATIAL_ACCEPTANCE_POLICY,
        "support_checksum": checksum_path(config.support_path(weather.h3_resolution)),
    }
    if path.exists():
        if json.loads(path.read_text()) != payload:
            raise ValueError(f"Conflicting acquisition row evidence: {path}")
    else:
        atomic_write_json(path, payload)
    core_key = _core_evidence_key(row)
    core_path = _row_evidence_path(weather.raw_dir, core_key)
    core_payload = {**payload, "row_identity": list(core_key), "scope": "core_only"}
    if not core_path.exists():
        atomic_write_json(core_path, core_payload)


def _has_live_row_evidence(config: Any, row: dict[str, Any]) -> bool:
    weather = config.surface_weather
    key = _row_evidence_key(row)
    path = _row_evidence_path(weather.raw_dir, key)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text())
        return (
            payload.get("row_identity") == list(key)
            and payload.get("method_version") == METHOD_VERSIONS["meteorological.surface_weather.download"]
            and payload.get("field_contract_version") == FIELD_CONTRACT_VERSION
            and payload.get("spatial_acceptance_policy") == SPATIAL_ACCEPTANCE_POLICY
            and payload.get("support_checksum") == checksum_path(config.support_path(weather.h3_resolution))
        )
    except (OSError, ValueError):
        return False


def _has_live_core_evidence(config: Any, row: dict[str, Any]) -> bool:
    weather = config.surface_weather
    key = _core_evidence_key(row)
    path = _row_evidence_path(weather.raw_dir, key)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text())
        return (
            payload.get("scope") == "core_only"
            and payload.get("row_identity") == list(key)
            and payload.get("method_version") == METHOD_VERSIONS["meteorological.surface_weather.download"]
            and payload.get("field_contract_version") == FIELD_CONTRACT_VERSION
            and payload.get("spatial_acceptance_policy") == SPATIAL_ACCEPTANCE_POLICY
            and payload.get("support_checksum") == checksum_path(config.support_path(weather.h3_resolution))
        )
    except (OSError, ValueError):
        return False


def _verified_source_evidence(config: Any) -> tuple[set[tuple], dict[str, str]]:
    """Read the canonical manifest and its exact inventory, never a workspace marker."""
    weather = config.surface_weather
    manifest_path = weather.acquisition_manifest_path
    inventory_path = weather.inventory_path
    if not manifest_path.exists() or not inventory_path.exists():
        raise ValueError("Current source acquisition manifest and inventory are required; reacquire unevidenced rows.")
    manifest = load_manifest(manifest_path, verify_artifacts=True)
    resolved = manifest.get("resolved_config", {})
    if (
        manifest.get("product") != "meteorological.surface_weather.download"
        or manifest.get("method_version") != METHOD_VERSIONS["meteorological.surface_weather.download"]
        or resolved.get("spatial_acceptance_policy") != SPATIAL_ACCEPTANCE_POLICY
        or resolved.get("field_contract_version") != FIELD_CONTRACT_VERSION
        or resolved.get("h3_resolution") != weather.h3_resolution
        or resolved.get("availability_lag_hours") != weather.availability_lag_hours
        or resolved.get("interval_hours") != weather.interval_hours
        or not any(item.get("checksum") == checksum_path(inventory_path)
                   for item in manifest["artifacts"])
    ):
        raise ValueError("Source acquisition method, field, spatial policy or inventory evidence does not match; reacquire or use the matching historical validator.")
    return {_row_evidence_key(row) for row in _load_inventory(inventory_path)}, {
        "manifest_path": str(manifest_path),
        "manifest_checksum": sha256_file(manifest_path),
        "inventory_checksum": checksum_path(inventory_path),
        "source_release_id": manifest["release_id"],
    }


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


def retrieval_matches_object(retrieval_uri: str | None, object_uri: str) -> bool:
    """Require the expected object key on a recorded HTTPS/S3 retrieval URI."""

    if not retrieval_uri:
        return False
    actual, logical = urlsplit(str(retrieval_uri)), urlsplit(object_uri)
    return (
        actual.scheme in {"https", "s3"}
        and bool(actual.netloc)
        and actual.path.endswith(logical.path)
    )


def valid_retrieval_time(value: object) -> bool:
    """Require a parseable UTC timestamp for a recorded source retrieval."""

    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return False
    return not pd.isna(parsed) and parsed.tzinfo is not None and parsed.utcoffset() == pd.Timedelta(0)


def _load_inventory(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    parquet = pq.ParquetFile(path)
    schema = parquet.schema_arrow
    if not any(schema.equals(candidate, check_metadata=False) for candidate in (
        INVENTORY_SCHEMA, LEGACY_HRRR_INVENTORY_SCHEMA, PRE_F01_INVENTORY_SCHEMA
    )):
        raise ValueError(f"R5 HRRR inventory has an incompatible schema: {path}")
    rows = parquet.read().to_pylist()
    for row in rows:
        row.setdefault("PRECIP_INIT_TIME_UTC", None)
        row.setdefault("PRECIP_FORECAST_HOUR", None)
        row.setdefault("PRECIP_SOURCE_URI", None)
        for name in (
            "SOURCE_OBJECT_URI", "SOURCE_RETRIEVED_AT_UTC",
            "PRECIP_OBJECT_URI", "PRECIP_RETRIEVED_AT_UTC",
        ):
            row.setdefault(name, None)
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
    availability_lag_hours: int | None = None,
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
    if set(frame["AVAILABILITY_POLICY"].astype(str)) != {AVAILABILITY_POLICY}:
        return False
    if set(frame["WIND_VECTOR_BASIS"].astype(str)) != {"earth_relative"}:
        return False
    if not set(frame["SOURCE_WIND_BASIS"].astype(str)).issubset({"grid_relative", "earth_relative"}):
        return False
    if valid_time_utc is not None:
        valid = pd.Timestamp(valid_time_utc)
        valid = valid.tz_localize("UTC") if valid.tzinfo is None else valid.tz_convert("UTC")
        if set(frame["VALID_TIME_UTC"].astype(str)) != {valid.isoformat()}:
            return False
        if availability_lag_hours is not None and set(frame["AVAILABLE_AT_UTC"].astype(str)) != {
            (valid + pd.Timedelta(hours=availability_lag_hours)).isoformat()
        }:
            return False
        if set(frame["PRECIP_VALID_TIME_UTC"].astype(str)) != {valid.isoformat()}:
            return False
        if set(frame["PRECIP_FORECAST_HOUR"].astype(int)) != {1}:
            return False
        precip_init = valid - pd.Timedelta(hours=1)
        if set(frame["PRECIP_INIT_TIME_UTC"].astype(str)) != {precip_init.isoformat()}:
            return False
    try:
        validate_fields(frame, SAMPLE_SCHEMA, context="HRRR acquisition sample")
    except ValueError:
        return False
    return True


def _reusable_core_sample(
    path: Path,
    support: pd.DataFrame,
    *,
    valid_time_utc: pd.Timestamp,
    availability_lag_hours: int,
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
        or set(frame["AVAILABILITY_POLICY"].astype(str)) != {AVAILABILITY_POLICY}
        or set(frame["WIND_VECTOR_BASIS"].astype(str)) != {"earth_relative"}
        or not set(frame["SOURCE_WIND_BASIS"].astype(str)).issubset({"grid_relative", "earth_relative"})
    ):
        return None
    valid = pd.Timestamp(valid_time_utc).tz_convert("UTC").isoformat()
    if set(frame["VALID_TIME_UTC"].astype(str)) != {valid}:
        return None
    expected_available = (
        pd.Timestamp(valid_time_utc).tz_convert("UTC")
        + pd.Timedelta(hours=availability_lag_hours)
    ).isoformat()
    if set(frame["AVAILABLE_AT_UTC"].astype(str)) != {expected_available}:
        return None
    try:
        validate_fields(frame, schema, context="Reusable HRRR core sample")
    except ValueError:
        return None
    return frame


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
    source_retrieved_at_utc: str | None = None,
    precip_retrieved_at_utc: str | None = None,
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
        "SOURCE_OBJECT_URI": hrrr_logical_object_uri(valid) if status == "COMPLETE" else None,
        "SOURCE_RETRIEVED_AT_UTC": source_retrieved_at_utc if status == "COMPLETE" else None,
        "PRECIP_OBJECT_URI": hrrr_logical_object_uri(valid, precip_forecast_hour) if status == "COMPLETE" else None,
        "PRECIP_RETRIEVED_AT_UTC": precip_retrieved_at_utc if status == "COMPLETE" else None,
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
    verified_new_rows: set[tuple] | None = None,
    source_evidence: tuple[set[tuple], dict[str, str]] | None = None,
) -> None:
    weather = config.surface_weather
    if source_evidence is None:
        source_evidence = _verified_source_evidence(config)
    source_keys, source_identity = source_evidence
    new_keys = verified_new_rows or set()
    if any(_row_evidence_key(row) not in source_keys | new_keys
           and not _has_live_row_evidence(config, row) for row in rows):
        raise ValueError("Acquisition rows lack exact current spatial-policy source evidence; reacquire unevidenced rows.")
    expected_times = _expected_times(start, end, weather.timezone, weather.interval_hours)
    expected_keys = {value.tz_convert("UTC").isoformat() for value in expected_times}
    observed_keys = [str(row["VALID_TIME_UTC"]) for row in rows]
    if len(observed_keys) != len(expected_keys) or set(observed_keys) != expected_keys:
        raise ValueError(
            "Canonical HRRR acquisition requires every valid time in its continuous "
            f"declared interval {start} through {end}, exactly once."
        )
    support = load_meteorological_support(weather.h3_resolution, config_path)
    validated_crosswalks: set[tuple[Path, str, str]] = set()
    for row in rows:
        valid_time = pd.Timestamp(str(row["VALID_TIME_UTC"]))
        expected_uri = hrrr_logical_object_uri(valid_time)
        expected_precip_uri = hrrr_logical_object_uri(valid_time, weather.precipitation_forecast_hour)
        if (
            row.get("STATUS") != "COMPLETE"
            or row.get("SOURCE_OBJECT_URI") != expected_uri
            or row.get("PRECIP_OBJECT_URI") != expected_precip_uri
            or not retrieval_matches_object(row.get("SOURCE_URI"), expected_uri)
            or not retrieval_matches_object(row.get("PRECIP_SOURCE_URI"), expected_precip_uri)
            or not valid_retrieval_time(row.get("SOURCE_RETRIEVED_AT_UTC"))
            or not valid_retrieval_time(row.get("PRECIP_RETRIEVED_AT_UTC"))
            or int(row.get("PRECIP_FORECAST_HOUR") or -1) != weather.precipitation_forecast_hour
        ):
            raise ValueError(
                "Canonical HRRR acquisition publication requires COMPLETE f00 core and f01 "
                f"precipitation provenance for {valid_time.isoformat()}."
            )
        expected_available = (
            valid_time + pd.Timedelta(hours=weather.availability_lag_hours)
        ).isoformat()
        if (
            row.get("LOCAL_DATE") != valid_time.tz_convert(weather.timezone).strftime("%Y-%m-%d")
            or row.get("AVAILABLE_AT_UTC") != expected_available
            or int(row.get("H3_CELL_COUNT") or -1) != len(support)
            or int(row.get("H3_RESOLUTION") or -1) != weather.h3_resolution
        ):
            raise ValueError(f"Retained HRRR row has incompatible release policy: {valid_time}.")
        sample_relative = Path(str(row.get("RELATIVE_PATH") or ""))
        crosswalk_relative = Path(str(row.get("CROSSWALK_RELATIVE_PATH") or ""))
        if any(path.is_absolute() or ".." in path.parts or not path.parts for path in (
            sample_relative, crosswalk_relative
        )):
            raise ValueError(f"Retained HRRR row has an unsafe artifact path: {valid_time}.")
        sample = resolve_raw_relative(weather.raw_dir, str(sample_relative))
        crosswalk = resolve_raw_relative(weather.raw_dir, str(crosswalk_relative))
        crosswalk_key = (
            crosswalk, str(row.get("CROSSWALK_CHECKSUM")), str(row.get("SOURCE_GRID_HASH"))
        )
        if crosswalk_key not in validated_crosswalks:
            if (
                not _valid_crosswalk(crosswalk, support, str(row.get("SOURCE_GRID_HASH")))
                or sha256_file(crosswalk) != row.get("CROSSWALK_CHECKSUM")
            ):
                raise ValueError(f"Retained HRRR sample or crosswalk is invalid: {valid_time}.")
            validated_crosswalks.add(crosswalk_key)
        if (
            not _valid_sample(
                sample, support, valid_time_utc=valid_time,
                availability_lag_hours=weather.availability_lag_hours,
            )
            or sha256_file(sample) != row.get("CHECKSUM")
        ):
            raise ValueError(f"Retained HRRR sample or crosswalk is invalid: {valid_time}.")
    if inventory_destination.parent.resolve() != weather.raw_dir.resolve():
        copied: set[tuple[str, str]] = set()
        for row in rows:
            for path_field, checksum_field in (
                ("RELATIVE_PATH", "CHECKSUM"),
                ("CROSSWALK_RELATIVE_PATH", "CROSSWALK_CHECKSUM"),
            ):
                reference = (str(row[path_field]), str(row[checksum_field]))
                if reference not in copied:
                    copy_referenced_object(
                        source_root=weather.raw_dir, target_root=inventory_destination.parent,
                        relative=reference[0], checksum=reference[1],
                    )
                    copied.add(reference)
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
                "field_contract_version": FIELD_CONTRACT_VERSION,
                "start_date": start,
                "end_date": end,
                "timezone": weather.timezone,
                "interval_hours": weather.interval_hours,
                "h3_resolution": weather.h3_resolution,
                "availability_lag_hours": weather.availability_lag_hours,
                "availability_policy": AVAILABILITY_POLICY,
                "spatial_acceptance_policy": SPATIAL_ACCEPTANCE_POLICY,
                "acquisition_evidence_policy": ACQUISITION_EVIDENCE_POLICY,
                "source_evidence": source_identity,
                "source": {
                    "model": HRRR_MODEL,
                    "product": HRRR_PRODUCT,
                    "forecast_hour": FORECAST_HOUR,
                    "precipitation_forecast_hour": weather.precipitation_forecast_hour,
                    "backend": "direct_grib_via_herbie",
                    "variable_selectors": HRRR_VARIABLES,
                },
                "sample_storage": "immutable-sha256-objects-v1; one strict H3 R5 Parquet file per valid time",
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
                "rule": "AVAILABLE_AT_UTC is an assumed fixed-lag policy timestamp, equal to VALID_TIME_UTC plus the configured lag; provider-observed availability is not measured.",
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


def _download_surface_weather_locked(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    overwrite: bool = False,
    max_workers: int = DEFAULT_HERBIE_WORKERS,
    dry_run: bool = False,
    allow_large_download: bool = False,
    run_id: str | None = None,
    working_inventory_path: str | Path | None = None,
    inventory_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    _run_state_path: Path | None = None,
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
    if len(times) > 42 and not allow_large_download:
        raise ValueError(
            f"Requested {len(times)} HRRR valid times for {start} through {end} "
            f"at R{weather.h3_resolution}. Preview with --dry-run, then pass "
            "--allow-large-download if this acquisition is intentional."
        )
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
    policy_marker = weather.raw_dir / "SPATIAL_ACCEPTANCE_POLICY.json"
    previous_manifest = (
        json.loads(manifest_destination.read_text(encoding="utf-8"))
        if manifest_destination.exists() else None
    )
    canonical_policy = (
        previous_manifest.get("resolved_config", {}).get("spatial_acceptance_policy")
        if previous_manifest else None
    )
    marker_policy = (
        json.loads(policy_marker.read_text(encoding="utf-8")).get("policy")
        if policy_marker.exists() else None
    )
    legacy_rows = bool(existing_rows) and (
        (previous_manifest is not None and canonical_policy != SPATIAL_ACCEPTANCE_POLICY)
        or marker_policy != SPATIAL_ACCEPTANCE_POLICY
    )
    if legacy_rows:
        if not overwrite:
            raise ValueError(
                "Existing HRRR rows predate spatial acceptance. Reacquire the frozen range "
                "with --overwrite before publishing a spatially validated inventory."
            )
        if previous_manifest is not None:
            old_range = previous_manifest.get("temporal_coverage", {})
            if (start, end) != (old_range.get("start_date"), old_range.get("end_date")):
                raise ValueError("Spatial-policy migration requires --overwrite of the full frozen range.")
        existing_rows = []
    if marker_policy != SPATIAL_ACCEPTANCE_POLICY:
        atomic_write_json(
            policy_marker, {"policy": SPATIAL_ACCEPTANCE_POLICY}, overwrite=policy_marker.exists()
        )
    if previous_manifest is not None and not legacy_rows:
        try:
            source_evidence = _verified_source_evidence(config)
        except ValueError:
            # Changed availability/support contracts force fresh acquisition;
            # they never license reuse under the former manifest's claims.
            source_evidence = (set(), {"mode": "current_run_native_grid_validation"})
    else:
        source_evidence = (set(), {"mode": "current_run_native_grid_validation"})
    evidenced_existing = source_evidence[0]
    evidenced_existing_core = (
        {_core_evidence_key(row) for row in _load_inventory(weather.inventory_path)}
        if evidenced_existing else set()
    )
    verified_new_rows: set[tuple] = set()
    existing_by_time = {str(row["VALID_TIME_UTC"]): row for row in existing_rows}
    crosswalk_cache: dict[str, tuple[pd.DataFrame, Path, str]] = {}
    crosswalk_validation_cache: dict[tuple[str, str], bool] = {}
    crosswalk_checksum_cache: dict[Path, str] = {}
    crosswalk_lock = Lock()

    def acquire(valid_time: pd.Timestamp) -> dict[str, object]:
        valid = pd.Timestamp(valid_time).tz_convert("UTC")
        valid_key = valid.isoformat()
        local_date = valid.tz_convert(weather.timezone).strftime("%Y-%m-%d")
        existing = existing_by_time.get(valid_key, {})
        destination = (
            resolve_raw_relative(weather.raw_dir, str(existing["RELATIVE_PATH"]))
            if existing.get("RELATIVE_PATH")
            else sample_path(weather.raw_dir, valid, weather.timezone)
        )
        precip_forecast_hour = weather.precipitation_forecast_hour
        expected_source_uri = hrrr_logical_object_uri(valid)
        expected_precip_uri = hrrr_logical_object_uri(valid, precip_forecast_hour)
        expected_checksum = str(existing.get("CHECKSUM") or "")
        expected_crosswalk_checksum = str(existing.get("CROSSWALK_CHECKSUM") or "")
        expected_hash = str(existing.get("SOURCE_GRID_HASH") or "") or None
        if expected_hash is None and _valid_sample(destination, support, valid_time_utc=valid, availability_lag_hours=weather.availability_lag_hours):
            expected_hash = str(
                pq.read_table(destination, columns=["SOURCE_GRID_HASH"])[0][0].as_py()
            )
        existing_crosswalk = (
            resolve_raw_relative(weather.raw_dir, str(existing["CROSSWALK_RELATIVE_PATH"]))
            if existing.get("CROSSWALK_RELATIVE_PATH")
            else (crosswalk_path(weather.raw_dir, expected_hash) if expected_hash else None)
        )
        actual_checksum = sha256_file(destination) if destination.exists() else ""
        checksum_ok = not expected_checksum or actual_checksum == expected_checksum
        validation_key = (str(existing_crosswalk), str(expected_hash))
        with crosswalk_lock:
            if existing_crosswalk is not None and existing_crosswalk not in crosswalk_checksum_cache:
                crosswalk_checksum_cache[existing_crosswalk] = (
                    sha256_file(existing_crosswalk) if existing_crosswalk.exists() else ""
                )
            actual_crosswalk_checksum = crosswalk_checksum_cache.get(existing_crosswalk, "")
            crosswalk_checksum_ok = (
                not expected_crosswalk_checksum
                or actual_crosswalk_checksum == expected_crosswalk_checksum
            )
            if validation_key not in crosswalk_validation_cache:
                crosswalk_validation_cache[validation_key] = (
                    existing_crosswalk is not None
                    and _valid_crosswalk(existing_crosswalk, support, expected_hash)
                )
            crosswalk_ok = crosswalk_validation_cache[validation_key] and crosswalk_checksum_ok
        source_provenance_ok = (
            existing.get("SOURCE_OBJECT_URI") == expected_source_uri
            and retrieval_matches_object(existing.get("SOURCE_URI"), expected_source_uri)
            and valid_retrieval_time(existing.get("SOURCE_RETRIEVED_AT_UTC"))
        )
        precip_provenance_ok = (
            existing.get("PRECIP_OBJECT_URI") == expected_precip_uri
            and retrieval_matches_object(existing.get("PRECIP_SOURCE_URI"), expected_precip_uri)
            and valid_retrieval_time(existing.get("PRECIP_RETRIEVED_AT_UTC"))
            and int(existing.get("PRECIP_FORECAST_HOUR") or -1) == precip_forecast_hour
        )
        if (
            not overwrite
            and (_row_evidence_key(existing) in evidenced_existing
                 or _has_live_row_evidence(config, existing))
            and checksum_ok
            and _valid_sample(destination, support, valid_time_utc=valid, availability_lag_hours=weather.availability_lag_hours)
            and crosswalk_ok
            and source_provenance_ok
            and precip_provenance_ok
        ):
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
                source_uri=str(existing["SOURCE_URI"]),
                precip_source_uri=str(existing["PRECIP_SOURCE_URI"]),
                source_retrieved_at_utc=str(existing["SOURCE_RETRIEVED_AT_UTC"]),
                precip_retrieved_at_utc=str(existing["PRECIP_RETRIEVED_AT_UTC"]),
                sample_checksum=actual_checksum,
                crosswalk_checksum=actual_crosswalk_checksum,
                source_grid_hash=expected_hash,
            )
        try:
            reusable_core = (
                _reusable_core_sample(destination, support, valid_time_utc=valid, availability_lag_hours=weather.availability_lag_hours)
                if not overwrite and checksum_ok and crosswalk_ok and source_provenance_ok
                and (_core_evidence_key(existing) in evidenced_existing_core
                     or _has_live_core_evidence(config, existing))
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
                        precip_retrieved_at = datetime.now(UTC).isoformat()
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
                repaired_path, repaired_checksum = write_immutable_table(
                    repaired, raw_dir=weather.raw_dir, family="samples", schema=SAMPLE_SCHEMA,
                    validate=lambda path: _valid_sample(
                        path, support, valid_time_utc=valid,
                        availability_lag_hours=weather.availability_lag_hours,
                    ),
                )
                repaired_row = _inventory_row(
                    valid_time=valid,
                    timezone=weather.timezone,
                    availability_lag_hours=weather.availability_lag_hours,
                    h3_resolution=weather.h3_resolution,
                    h3_cell_count=len(support),
                    raw_dir=weather.raw_dir,
                    sample=repaired_path,
                    crosswalk=existing_crosswalk,
                    status="COMPLETE",
                    source_uri=str(existing["SOURCE_URI"]),
                    precip_source_uri=precip_source_uri,
                    source_retrieved_at_utc=str(existing["SOURCE_RETRIEVED_AT_UTC"]),
                    precip_retrieved_at_utc=precip_retrieved_at,
                    source_grid_hash=expected_hash,
                    sample_checksum=repaired_checksum,
                    crosswalk_checksum=actual_crosswalk_checksum,
                )
                with crosswalk_lock:
                    verified_new_rows.add(_row_evidence_key(repaired_row))
                _record_live_row_evidence(config, repaired_row)
                return repaired_row
            for attempt in range(1, HERBIE_ATTEMPTS_PER_TIMESTAMP + 1):
                try:
                    source_grid, source_uri = fetch_cropped_hrrr_grid(
                        valid_time_utc=valid,
                        bbox=config.bbox,
                        bbox_padding_degrees=weather.bbox_padding_degrees,
                        availability_lag_hours=weather.availability_lag_hours,
                    )
                    source_retrieved_at = datetime.now(UTC).isoformat()
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
                    precip_retrieved_at = datetime.now(UTC).isoformat()
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
            with crosswalk_lock:
                cached = crosswalk_cache.get(grid_hash)
                if cached is not None:
                    crosswalk, crosswalk_destination, crosswalk_checksum = cached
                elif expected_hash == grid_hash and crosswalk_ok and existing_crosswalk is not None:
                    crosswalk_destination = existing_crosswalk
                    crosswalk_checksum = actual_crosswalk_checksum
                    crosswalk = pq.read_table(existing_crosswalk, schema=CROSSWALK_SCHEMA).to_pandas()
                else:
                    crosswalk = build_nearest_grid_crosswalk(support, source_grid)
                    crosswalk_destination, crosswalk_checksum = write_immutable_table(
                        crosswalk, raw_dir=weather.raw_dir, family="crosswalks",
                        schema=CROSSWALK_SCHEMA,
                        validate=lambda path: _valid_crosswalk(path, support, grid_hash),
                    )
                crosswalk_cache[grid_hash] = (crosswalk, crosswalk_destination, crosswalk_checksum)
            validate_crosswalk_distance(crosswalk, source_grid, support)
            sampled = sample_source_grid(
                source_grid,
                crosswalk,
                local_date=local_date,
                valid_time_utc=valid,
                precip_init_time_utc=valid - pd.Timedelta(hours=precip_forecast_hour),
                precip_forecast_hour=precip_forecast_hour,
            )
            published_sample, published_checksum = write_immutable_table(
                sampled, raw_dir=weather.raw_dir, family="samples", schema=SAMPLE_SCHEMA,
                validate=lambda path: _valid_sample(
                    path, support, valid_time_utc=valid,
                    availability_lag_hours=weather.availability_lag_hours,
                ),
            )
            acquired_row = _inventory_row(
                valid_time=valid,
                timezone=weather.timezone,
                availability_lag_hours=weather.availability_lag_hours,
                h3_resolution=weather.h3_resolution,
                h3_cell_count=len(support),
                raw_dir=weather.raw_dir,
                sample=published_sample,
                crosswalk=crosswalk_destination,
                status="COMPLETE",
                source_uri=source_uri,
                precip_source_uri=precip_source_uri,
                source_retrieved_at_utc=source_retrieved_at,
                precip_retrieved_at_utc=precip_retrieved_at,
                source_grid_hash=grid_hash,
                sample_checksum=published_checksum,
                crosswalk_checksum=crosswalk_checksum,
            )
            with crosswalk_lock:
                verified_new_rows.add(_row_evidence_key(acquired_row))
            _record_live_row_evidence(config, acquired_row)
            return acquired_row
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
        if _run_state_path is not None:
            _atomic_table_write(
                pa.Table.from_pylist(merged, schema=INVENTORY_SCHEMA).to_pandas(),
                _run_state_path.parent / "WORKING_INVENTORY.parquet", INVENTORY_SCHEMA,
            )
        return merged

    def record(row: dict[str, object]) -> None:
        requested_rows.append(row)
        if len(times) <= 100:
            checkpoint()
        if len(requested_rows) % 100 == 0:
            if len(times) > 100:
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
        verified_new_rows=verified_new_rows,
        source_evidence=source_evidence,
    )
    summary["inventory_path"] = str(inventory_destination)
    summary["manifest_path"] = str(manifest_destination)
    if use_latest_freeze:
        freeze_path.unlink(missing_ok=True)
    return summary


def download_surface_weather(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    overwrite: bool = False,
    max_workers: int = DEFAULT_HERBIE_WORKERS,
    dry_run: bool = False,
    allow_large_download: bool = False,
    run_id: str | None = None,
    working_inventory_path: str | Path | None = None,
    inventory_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, object]:
    """Own the raw workspace through recovery, acquisition, and metadata commit."""

    options = dict(
        start_date=start_date, end_date=end_date, overwrite=overwrite,
        max_workers=max_workers, dry_run=dry_run,
        allow_large_download=allow_large_download, run_id=run_id,
        working_inventory_path=working_inventory_path, inventory_path=inventory_path,
        manifest_path=manifest_path,
    )
    if dry_run:
        return _download_surface_weather_locked(config_path, **options)
    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    inventory_parent = Path(inventory_path or weather.inventory_path).parent.resolve()
    manifest_parent = Path(manifest_path or weather.acquisition_manifest_path).parent.resolve()
    if inventory_parent != manifest_parent:
        raise ValueError("Acquisition inventory and manifest must share one publication directory.")
    publication_parent = inventory_parent
    other_owner = (
        acquisition_lock(publication_parent, writer=True)
        if publication_parent.resolve() != weather.raw_dir.resolve() else nullcontext()
    )
    with acquisition_lock(weather.raw_dir, writer=True), other_owner:
        TransactionalFamilyPublisher.recover(weather.raw_dir)
        if publication_parent.resolve() != weather.raw_dir.resolve():
            TransactionalFamilyPublisher.recover(publication_parent)
        token = uuid.uuid4().hex
        state_path = weather.raw_dir / "runs" / token / "RUN_STATE.json"
        state = {"schema_version": 1, "run_id": run_id or token,
                 "status": "ACQUIRING", "started_at_utc": datetime.now(UTC).isoformat()}
        atomic_write_json(state_path, state)
        try:
            result = _download_surface_weather_locked(
                config_path, **options, _run_state_path=state_path,
            )
        except BaseException as exc:
            state.update(status="INCOMPLETE", failure=f"{type(exc).__name__}: {exc}")
            atomic_write_json(state_path, state, overwrite=True)
            raise
        state.update(status="COMMITTED", completed_at_utc=datetime.now(UTC).isoformat())
        atomic_write_json(state_path, state, overwrite=True)
        result["run_state_path"] = str(state_path)
        return result


def _snapshot_existing_surface_weather_acquisition_locked(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    working_inventory_path: str | Path | None = None,
    inventory_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    """Publish a network-free snapshot with its retained retrieval provenance."""

    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    source_inventory = (
        weather.inventory_path if weather.inventory_path.exists()
        else weather.working_inventory_path
    )
    if not source_inventory.exists():
        raise FileNotFoundError(
            "Cannot reconstruct actual HRRR retrieval provenance from samples alone; "
            "a current acquisition inventory is required for a network-free snapshot."
        )
    rows = _load_inventory(source_inventory)
    dates = sorted({str(row["LOCAL_DATE"]) for row in rows})
    if not dates:
        raise FileNotFoundError(f"No existing R5 HRRR inventory rows: {source_inventory}")
    expected_dates = [
        value.strftime("%Y-%m-%d") for value in pd.date_range(dates[0], dates[-1], freq="D")
    ]
    if dates != expected_dates:
        missing = sorted(set(expected_dates).difference(dates))
        raise ValueError(f"Existing R5 HRRR samples contain a date gap: {missing[0]}")
    candidate_working = Path(working_inventory_path or weather.working_inventory_path)
    candidate_inventory = Path(inventory_path or weather.inventory_path)
    candidate_manifest = Path(manifest_path or weather.acquisition_manifest_path)
    source_evidence = _verified_source_evidence(config)
    if any(_row_evidence_key(row) not in source_evidence[0] for row in rows):
        raise ValueError("Snapshot inventory differs from the verified source acquisition; reacquire unevidenced rows.")
    _atomic_table_write(
        pa.Table.from_pylist(rows, schema=INVENTORY_SCHEMA).to_pandas(),
        candidate_working, INVENTORY_SCHEMA,
    )
    _publish_complete_acquisition(
        config_path=config.path, config=config, rows=rows,
        inventory_destination=candidate_inventory, manifest_destination=candidate_manifest,
        start=dates[0], end=dates[-1],
        run_id=run_id or f"hrrr-r5-snapshot-{uuid.uuid4().hex[:12]}",
        source_evidence=source_evidence,
    )
    return {
        "snapshot_mode": "existing_validated_r5_samples",
        "start_date": dates[0], "end_date": dates[-1],
        "expected_times": len(_expected_times(dates[0], dates[-1], weather.timezone, weather.interval_hours)),
        "complete_times": len(rows), "failed_times": 0,
        "working_inventory_path": str(candidate_working),
        "inventory_path": str(candidate_inventory),
        "manifest_path": str(candidate_manifest),
    }


def snapshot_existing_surface_weather_acquisition(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    working_inventory_path: str | Path | None = None,
    inventory_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    """Publish inventory-driven existing objects under exclusive acquisition ownership."""

    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    inventory_parent = Path(inventory_path or weather.inventory_path).parent.resolve()
    manifest_parent = Path(manifest_path or weather.acquisition_manifest_path).parent.resolve()
    if inventory_parent != manifest_parent:
        raise ValueError("Acquisition inventory and manifest must share one publication directory.")
    publication_parent = inventory_parent
    other_owner = (
        acquisition_lock(publication_parent, writer=True)
        if publication_parent.resolve() != weather.raw_dir.resolve() else nullcontext()
    )
    with acquisition_lock(weather.raw_dir, writer=True), other_owner:
        TransactionalFamilyPublisher.recover(weather.raw_dir)
        if publication_parent.resolve() != weather.raw_dir.resolve():
            TransactionalFamilyPublisher.recover(publication_parent)
        return _snapshot_existing_surface_weather_acquisition_locked(
            config_path, working_inventory_path=working_inventory_path,
            inventory_path=inventory_path, manifest_path=manifest_path, run_id=run_id,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--workers", type=int, default=DEFAULT_HERBIE_WORKERS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-large-download", action="store_true")
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
            allow_large_download=args.allow_large_download,
            run_id=args.run_id,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
