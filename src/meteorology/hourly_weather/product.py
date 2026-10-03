"""Offline acquisition and publication of distinct hourly f00 H3 atmosphere.

Input bundles are retained normalized grids from the existing HRRR decoder, one
Parquet and provenance JSON per UTC hour. This route never fetches source bytes.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
import fcntl
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from shapely import from_wkb

from meteorology.core.artifacts import TransactionalFamilyPublisher
from meteorology.core.config.paths import project_root
from meteorology.core.data.meteorological_schemas import (
    HOURLY_WEATHER_INVENTORY_SCHEMA, HOURLY_WEATHER_SCHEMA, HRRR_CROSSWALK_SCHEMA,
)

from ..artifacts import (
    cell_set_hash, checksum_path, load_manifest, manifest_payload, parquet_contract,
    resolve_portable_path, write_manifest, write_table,
)
from ..core.artifacts import atomic_write_json
from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from ..field_contracts import FIELD_CONTRACT_VERSION, validate_fields
from ..spatial_support.build import SUPPORT_METHOD, load_meteorological_support
from ..surface_weather.sampling import (
    AVAILABILITY_POLICY, SPATIAL_ACCEPTANCE_POLICY, build_nearest_grid_crosswalk,
)
from ..surface_weather.source import RAW_SCHEMA, _grid_hash, hrrr_logical_object_uri
from ..surface_weather.storage import acquisition_lock, write_immutable_table
from ..surface_weather.storage import (
    acquisition_read_locks, snapshot_acquisition_metadata, snapshot_support_inputs,
)
from ..temporal_products import HOURLY_CORE, _utc, hourly_utc_instants, validate_hourly_records

ACQUISITION_PRODUCT = "meteorological.hourly_weather.acquire"
HOURLY_PRODUCT = "meteorological.hourly_weather"


def _paths(config: Any) -> tuple[Path, Path, Path, Path]:
    path = config.path.resolve()
    if path.parent.name == "data" and path.parent.parent.name == "config":
        root = path.parent.parent.parent
    elif os.environ.get("METEOROLOGY_WORKSPACE"):
        root = project_root()
    else:
        raise ValueError("Custom hourly configuration requires METEOROLOGY_WORKSPACE for output paths.")
    raw = root / "data/raw/environment/meteorological/hourly_weather/hrrr"
    processed = root / "data/processed/domain/environmental_layer/meteorological/hourly_weather"
    return (raw, raw / "HOURLY_SOURCE_INVENTORY.parquet",
            raw / "ACQUISITION_MANIFEST.json", processed)


def _source_metadata(kind: str, local_date: str) -> list[dict]:
    if kind == "synthetic_fixture":
        return [dict(name="Synthetic hourly HRRR-shaped fixture", license="test fixture",
                     attribution="toolkit-meteorology test", observation_period=local_date,
                     redistribution_restrictions="none")]
    return [dict(name="NOAA High-Resolution Rapid Refresh surface analysis",
                 license="United States government data; consult NOAA source terms",
                 attribution="NOAA/NCEP HRRR", observation_period=local_date,
                 redistribution_restrictions="Consult authoritative NOAA archive terms")]


def _bundle_paths(decoded_dir: Path, valid: Any) -> tuple[Path, Path]:
    stem = valid.strftime("%Y%m%dT%HZ")
    committed = decoded_dir / stem
    if committed.exists():
        return committed / "grid.parquet", committed / "evidence.json"
    return decoded_dir / f"{stem}.parquet", decoded_dir / f"{stem}.json"


def retain_decoded_hourly_grid(grid: pd.DataFrame, *, valid_time_utc: str,
                               source_uri: str, retrieved_at_utc: str,
                               output_dir: str | Path,
                               source_evidence_kind: str = "retained_decoded_hrrr") -> tuple[Path, Path]:
    """Save a result of the existing HRRR decoder with its native-grid evidence.

    This function does not fetch GRIB bytes. A budgeted caller may use
    ``fetch_cropped_hrrr_grid`` and pass its returned frame and retrieval URI.
    """
    valid = _utc(valid_time_utc)
    if valid is None or valid.minute or valid.second or valid.microsecond:
        raise ValueError("Retained hourly grid requires a whole UTC hour.")
    footprint = grid.attrs.get("native_footprint")
    allowance = grid.attrs.get("max_nearest_distance_m")
    if footprint is None or allowance is None:
        raise ValueError("Existing decoder's native footprint and spacing evidence are required.")
    if source_evidence_kind not in {"retained_decoded_hrrr", "synthetic_fixture"}:
        raise ValueError("Unknown retained hourly source evidence kind.")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    stem = valid.strftime("%Y%m%dT%HZ")
    committed = output / stem
    legacy_parquet, legacy_evidence = output / f"{stem}.parquet", output / f"{stem}.json"
    if set(grid.columns) != set(RAW_SCHEMA.names):
        raise ValueError("Retained grid must use the existing normalized HRRR raw schema.")
    values = grid[RAW_SCHEMA.names].copy()
    values.attrs.clear()
    table = pa.Table.from_pandas(values, schema=RAW_SCHEMA,
                                 preserve_index=False, safe=True)
    object_uri = (hrrr_logical_object_uri(valid)
                  if source_evidence_kind == "retained_decoded_hrrr"
                  else f"synthetic://fixture/{valid:%Y%m%dT%HZ}")
    evidence = dict(valid_time_utc=valid.isoformat(),
                    source_grid_hash=str(grid["SOURCE_GRID_HASH"].iloc[0]),
                    source_uri=source_uri, source_object_uri=object_uri,
                    retrieved_at_utc=retrieved_at_utc,
                    source_evidence_kind=source_evidence_kind,
                    native_footprint_wkb_hex=footprint.wkb_hex,
                    max_nearest_distance_m=float(allowance))
    lag_hours = int((_utc(str(grid["AVAILABLE_AT_UTC"].iloc[0])) - valid).total_seconds() / 3600)
    lock_path = output / f".{stem}.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if legacy_parquet.exists() or legacy_evidence.exists():
            raise FileExistsError("Legacy retained hourly decoder bundle already exists.")
        candidate = output / f".{stem}.{uuid.uuid4().hex}.candidate"
        candidate.mkdir()
        try:
            parquet_path, evidence_path = candidate / "grid.parquet", candidate / "evidence.json"
            write_table(parquet_path, table, RAW_SCHEMA)
            atomic_write_json(evidence_path, evidence, overwrite=False)
            _load_decoded_grid(parquet_path, evidence_path, valid=valid, lag_hours=lag_hours)
            if committed.exists():
                existing_parquet, existing_evidence = _bundle_paths(output, valid)
                _load_decoded_grid(existing_parquet, existing_evidence, valid=valid,
                                   lag_hours=lag_hours)
                if (checksum_path(parquet_path) == checksum_path(existing_parquet)
                        and checksum_path(evidence_path) == checksum_path(existing_evidence)):
                    return existing_parquet, existing_evidence
                raise FileExistsError("Retained hourly decoder bundle already exists with different bytes.")
            os.replace(candidate, committed)
            return committed / "grid.parquet", committed / "evidence.json"
        finally:
            if candidate.exists():
                shutil.rmtree(candidate)


def _retain_file(source: Path, raw_dir: Path) -> tuple[Path, str]:
    """Copy external decoded evidence to a private content-addressed immutable file."""
    digest = checksum_path(source)
    parent = raw_dir / "objects" / "decoded"
    if parent.is_symlink():
        raise ValueError("Hourly decoded-object directory cannot be a symlink.")
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / f"{digest}{source.suffix}"
    temporary = parent / f".{uuid.uuid4().hex}.part"
    try:
        shutil.copy2(source, temporary)
        if checksum_path(temporary) != digest:
            raise ValueError("Decoded source input changed while being retained.")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or checksum_path(destination) != digest:
                raise ValueError("Retained decoded source content address is corrupted.")
        return destination, digest
    finally:
        temporary.unlink(missing_ok=True)


def _load_decoded_grid(parquet_path: Path, metadata_path: Path, *, valid: Any,
                       lag_hours: int) -> tuple[pd.DataFrame, dict]:
    if not parquet_path.is_file() or not metadata_path.is_file():
        raise FileNotFoundError(f"Missing retained hourly decoded bundle: {parquet_path}")
    parquet = pq.ParquetFile(parquet_path)
    if not parquet.schema_arrow.equals(RAW_SCHEMA, check_metadata=False):
        raise ValueError("Retained hourly grid differs from the existing normalized HRRR decoder schema.")
    grid = parquet.read().to_pandas()
    evidence = json.loads(metadata_path.read_text(encoding="utf-8"))
    expected_valid = valid.isoformat()
    if (grid.empty or grid["SOURCE_GRID_INDEX"].duplicated().any()
            or not np.array_equal(grid["SOURCE_GRID_INDEX"].to_numpy(),
                                  np.arange(len(grid)))):
        raise ValueError("Retained hourly source grid is empty or has duplicate/nonconsecutive native points.")
    if (set(grid["VALID_TIME_UTC"].astype(str)) != {expected_valid}
            or set(grid["INIT_TIME_UTC"].astype(str)) != {expected_valid}
            or set(grid["FORECAST_HOUR"].astype(int)) != {0}
            or set(grid["SOURCE_PRODUCT"].astype(str)) != {"sfc"}
            or set(grid["AVAILABLE_AT_UTC"].astype(str)) != {
                (valid + timedelta(hours=lag_hours)).isoformat()}):
        raise ValueError("Retained hourly grid is not the requested f00 analysis or availability policy.")
    if grid.select_dtypes(include="number").isna().any().any() or not np.isfinite(
            grid.select_dtypes(include="number").to_numpy(dtype=float)).all():
        raise ValueError("Retained hourly grid contains missing or non-finite source values.")
    grid_hashes = set(grid["SOURCE_GRID_HASH"].astype(str))
    models = set(grid["SOURCE_MODEL"].astype(str))
    if len(grid_hashes) != 1 or models not in ({"hrrr"}, {"synthetic_hrrr"}):
        raise ValueError("Retained hourly grid has mixed or unsupported source identity.")
    if next(iter(grid_hashes)) != _grid_hash(grid["SOURCE_LAT"], grid["SOURCE_LON"]):
        raise ValueError("Retained hourly source grid hash differs from native coordinates.")
    kind = evidence.get("source_evidence_kind")
    if (kind, next(iter(models))) not in {
        ("synthetic_fixture", "synthetic_hrrr"), ("retained_decoded_hrrr", "hrrr")
    }:
        raise ValueError("Retained hourly source evidence kind disagrees with its grid.")
    if evidence.get("valid_time_utc") != expected_valid or evidence.get("source_grid_hash") not in grid_hashes:
        raise ValueError("Retained hourly evidence time/grid differs from its Parquet bytes.")
    source_uri, object_uri = evidence.get("source_uri"), evidence.get("source_object_uri")
    if not isinstance(source_uri, str) or not source_uri or not isinstance(object_uri, str):
        raise ValueError("Retained hourly source retrieval/object identity is absent.")
    if kind == "retained_decoded_hrrr":
        from ..surface_weather.download import retrieval_matches_object

        expected_object = hrrr_logical_object_uri(valid)
        if object_uri != expected_object or not retrieval_matches_object(source_uri, object_uri):
            raise ValueError("Retained hourly HRRR source URI does not match its f00 object.")
    elif not source_uri.startswith("synthetic://") or not object_uri.startswith("synthetic://"):
        raise ValueError("Synthetic hourly source URI must remain explicitly synthetic.")
    retrieved = _utc(evidence.get("retrieved_at_utc"))
    if retrieved is None or (kind == "retained_decoded_hrrr" and retrieved < valid):
        raise ValueError("Retained hourly retrieval time is absent or precedes the source cycle.")
    allowance = evidence.get("max_nearest_distance_m")
    if not isinstance(allowance, (int, float)) or not np.isfinite(allowance) or allowance <= 0:
        raise ValueError("Retained hourly native spacing allowance is invalid.")
    try:
        footprint = from_wkb(bytes.fromhex(evidence["native_footprint_wkb_hex"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("Retained hourly native footprint is unavailable.") from exc
    if footprint is None or footprint.is_empty or not footprint.is_valid:
        raise ValueError("Retained hourly native footprint is invalid.")
    grid.attrs["native_footprint"] = footprint
    grid.attrs["max_nearest_distance_m"] = float(allowance)
    return grid, evidence


def _sample(grid: pd.DataFrame, crosswalk: pd.DataFrame, evidence: dict,
            *, local_date: str) -> pd.DataFrame:
    ordered = crosswalk.sort_values("H3_INDEX", ignore_index=True)
    native = grid.set_index("SOURCE_GRID_INDEX", drop=False)
    requested = ordered["SOURCE_GRID_INDEX"].to_numpy(dtype="int32")
    if not set(requested).issubset(native.index):
        raise ValueError("Hourly crosswalk references an absent native point.")
    source = native.loc[requested].reset_index(drop=True)
    frame = pd.DataFrame({
        "H3_INDEX": ordered["H3_INDEX"].astype(str), "DATE": local_date,
        "VALID_TIME_UTC": source["VALID_TIME_UTC"],
        "INIT_TIME_UTC": source["INIT_TIME_UTC"],
        "AVAILABLE_AT_UTC": source["AVAILABLE_AT_UTC"],
        "AVAILABILITY_POLICY": AVAILABILITY_POLICY,
        "TEMPERATURE_2M_C": source["TEMPERATURE_2M_K"].to_numpy(dtype=float) - 273.15,
        "RELATIVE_HUMIDITY_2M_PCT": source["RELATIVE_HUMIDITY_2M_PCT"],
        "U_WIND_10M_MS": source["U_WIND_10M_MS"],
        "V_WIND_10M_MS": source["V_WIND_10M_MS"],
        "WIND_VECTOR_BASIS": "earth_relative",
        "SOURCE_WIND_BASIS": source["SOURCE_WIND_BASIS"],
        "WIND_GUST_SURFACE_MS": source["WIND_GUST_SURFACE_MS"],
        "VISIBILITY_KM": source["VISIBILITY_M"].to_numpy(dtype=float) / 1000,
        "TOTAL_CLOUD_COVER_PCT": source["TOTAL_CLOUD_COVER_PCT"],
        "MEAN_SEA_LEVEL_PRESSURE_HPA": source["MEAN_SEA_LEVEL_PRESSURE_PA"].to_numpy(dtype=float) / 100,
        "SOURCE_GRID_HASH": source["SOURCE_GRID_HASH"],
        "SOURCE_GRID_INDEX": requested,
        "SOURCE_GRID_DISTANCE_M": ordered["SOURCE_GRID_DISTANCE_M"],
        "SOURCE_MODEL": source["SOURCE_MODEL"], "SOURCE_PRODUCT": source["SOURCE_PRODUCT"],
        "FORECAST_HOUR": source["FORECAST_HOUR"], "SOURCE_DATA_STATE": "COMPLETE",
        "SOURCE_OBJECT_URI": evidence["source_object_uri"],
        "SOURCE_URI": evidence["source_uri"],
        "SOURCE_RETRIEVED_AT_UTC": evidence["retrieved_at_utc"],
        "SOURCE_EVIDENCE_KIND": evidence["source_evidence_kind"],
    })
    frame["WIND_SPEED_10M_MS"] = np.hypot(frame["U_WIND_10M_MS"], frame["V_WIND_10M_MS"])
    frame = frame[HOURLY_WEATHER_SCHEMA.names]
    validate_fields(frame, HOURLY_WEATHER_SCHEMA, context="Hourly f00 sample")
    if len(frame) != len(ordered) or frame["H3_INDEX"].duplicated().any():
        raise ValueError("Hourly sample does not cover the H3 support once.")
    return frame


def _records(frame: pd.DataFrame) -> list[dict]:
    return [dict(valid_time_utc=row.VALID_TIME_UTC, init_time_utc=row.INIT_TIME_UTC,
                 available_at_utc=row.AVAILABLE_AT_UTC,
                 availability_policy=row.AVAILABILITY_POLICY,
                 forecast_hour=int(row.FORECAST_HOUR),
                 source_model="HRRR" if row.SOURCE_MODEL == "hrrr" else "synthetic_hrrr",
                 source_product=row.SOURCE_PRODUCT, H3_INDEX=row.H3_INDEX,
                 **{field: getattr(row, field) for field in HOURLY_CORE})
            for row in frame.itertuples(index=False)]


def _inputs_by_role(manifest: dict, role: str, *, base: Path) -> list[tuple[dict, Path]]:
    return [(entry, resolve_portable_path(entry["path"], base=base))
            for entry in manifest["inputs"] if entry.get("role") == role]


def acquire_hourly_weather(config_path: str | Path = DEFAULT_CONFIG_PATH, *,
                           local_date: str, decoded_dir: str | Path,
                           dry_run: bool = False, run_id: str | None = None) -> dict:
    """Publish one complete local day from retained decoded f00 grids; no network."""
    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    valid_times = hourly_utc_instants(local_date, weather.timezone)
    decoded = Path(decoded_dir).resolve()
    source_paths = [_bundle_paths(decoded, valid) for valid in valid_times]
    sizes = [path.stat().st_size for pair in source_paths for path in pair if path.exists()]
    preview = dict(local_date=local_date, expected_cycles=len(valid_times),
                   decoded_input_bytes=sum(sizes), source_requests=0,
                   network_bytes=0, provider_cycle_estimate=len(valid_times),
                   provider_byte_estimate=None,
                   provider_byte_estimate_status="unknown_without_verified_source_transfer_measurement",
                   complete_input_files=len(sizes) == len(valid_times) * 2)
    if dry_run:
        return preview
    if not preview["complete_input_files"]:
        raise FileNotFoundError("Hourly decoded input is incomplete; run --dry-run to inspect the request.")
    raw_dir, inventory_path, manifest_path, _ = _paths(config)
    support = load_meteorological_support(weather.h3_resolution, config.path)
    support_cells = set(support["H3_INDEX"].astype(str))
    rows: list[dict] = []
    inputs = [dict(role="support", path=str(config.support_path(weather.h3_resolution)),
                   checksum=checksum_path(config.support_path(weather.h3_resolution))),
              dict(role="support_manifest", path=str(config.support_manifest_path),
                   checksum=checksum_path(config.support_manifest_path))]
    run_id = run_id or f"hourly-acquire-{uuid.uuid4().hex[:12]}"
    with acquisition_lock(raw_dir, writer=True):
        for valid, (source_path, evidence_path) in zip(valid_times, source_paths, strict=True):
            retained_source, source_digest = _retain_file(source_path, raw_dir)
            retained_evidence, evidence_digest = _retain_file(evidence_path, raw_dir)
            grid, evidence = _load_decoded_grid(retained_source, retained_evidence, valid=valid,
                                                lag_hours=weather.availability_lag_hours)
            crosswalk = build_nearest_grid_crosswalk(support, grid)
            sample = _sample(grid, crosswalk, evidence, local_date=local_date)
            for kind, frame, schema in (("samples", sample, HOURLY_WEATHER_SCHEMA),
                                        ("crosswalks", crosswalk, HRRR_CROSSWALK_SCHEMA)):
                path, digest = write_immutable_table(
                    frame, raw_dir=raw_dir, family=kind, schema=schema,
                    validate=lambda candidate, expected=schema: pq.read_schema(candidate).equals(
                        expected, check_metadata=False),
                )
                inputs.append(dict(role=kind[:-1] if kind == "samples" else "crosswalk",
                                   valid_time_utc=valid.isoformat() if kind == "samples" else None,
                                   path=str(path), checksum=digest))
                if kind == "samples":
                    sample_digest = digest
                else:
                    crosswalk_digest = digest
            for role, retained, digest in (("decoded_grid", retained_source, source_digest),
                                           ("decoded_evidence", retained_evidence, evidence_digest)):
                inputs.append(dict(role=role, valid_time_utc=valid.isoformat(),
                                   path=str(retained), checksum=digest))
            rows.append(dict(LOCAL_DATE=local_date, VALID_TIME_UTC=valid.isoformat(),
                             INIT_TIME_UTC=valid.isoformat(),
                             AVAILABLE_AT_UTC=(valid + timedelta(hours=weather.availability_lag_hours)).isoformat(),
                             SAMPLE_CHECKSUM=sample_digest,
                             CROSSWALK_CHECKSUM=crosswalk_digest,
                             SOURCE_GRID_HASH=str(grid["SOURCE_GRID_HASH"].iloc[0]),
                             H3_CELL_COUNT=len(support_cells),
                             SOURCE_OBJECT_URI=evidence["source_object_uri"],
                             SOURCE_URI=evidence["source_uri"],
                             SOURCE_RETRIEVED_AT_UTC=evidence["retrieved_at_utc"],
                             SOURCE_EVIDENCE_KIND=evidence["source_evidence_kind"],
                             DECODED_INPUT_CHECKSUM=source_digest))
        kinds = {row["SOURCE_EVIDENCE_KIND"] for row in rows}
        if len(kinds) != 1:
            raise ValueError("One hourly acquisition cannot mix synthetic and retained HRRR evidence.")
        source_kind = kinds.pop()
        inventory = pd.DataFrame(rows)[HOURLY_WEATHER_INVENTORY_SCHEMA.names]
        with TransactionalFamilyPublisher(raw_dir, run_id=run_id) as publisher:
            staged_inventory = publisher.stage_path(inventory_path)
            staged_manifest = publisher.stage_manifest_path(manifest_path)
            write_table(staged_inventory, pa.Table.from_pandas(inventory, preserve_index=False),
                        HOURLY_WEATHER_INVENTORY_SCHEMA)
            payload = manifest_payload(
                product=ACQUISITION_PRODUCT, run_id=run_id, config_path=config.path,
                resolved_config=dict(local_date=local_date, timezone=weather.timezone,
                                     h3_resolution=weather.h3_resolution,
                                     availability_lag_hours=weather.availability_lag_hours,
                                     availability_policy=AVAILABILITY_POLICY,
                                     spatial_acceptance_policy=SPATIAL_ACCEPTANCE_POLICY,
                                     field_contract_version=FIELD_CONTRACT_VERSION,
                                     source_evidence_kind=source_kind,
                                     required_fields=list(HOURLY_CORE), optional_fields=[],
                                     support_cell_set_hash=cell_set_hash(support_cells)),
                artifacts=[parquet_contract(staged_inventory, published_path=inventory_path)],
                inputs=inputs, sources=_source_metadata(source_kind, local_date),
                h3_resolution=weather.h3_resolution, spatial_bounds=config.bbox,
                temporal_coverage=dict(start_date=local_date, end_date=local_date,
                                       expected_hours=len(valid_times)),
                availability_semantics=dict(policy=AVAILABILITY_POLICY,
                    note="Assumed fixed lag from valid time; provider publication unmeasured; retrieval separate."),
                limitations=["Offline retained decoded input; source compatibility requires separately reviewed real GRIB evidence.",
                             "No precipitation amount or rate is included."],
            )
            write_manifest(staged_manifest, payload)
            publisher.publish()
    return {**preview, "inventory": str(inventory_path), "manifest": str(manifest_path),
            "release_id": payload["release_id"], "source_evidence_kind": source_kind}


def build_hourly_weather(config_path: str | Path = DEFAULT_CONFIG_PATH, *,
                         acquisition_manifest: str | Path | None = None,
                         run_id: str | None = None) -> Path:
    """Publish separate H3-hour rows from a complete immutable input generation."""
    config = load_meteorological_config(config_path)
    raw_dir, _, default_manifest, processed = _paths(config)
    source_manifest_path = Path(acquisition_manifest or default_manifest).resolve()
    with TransactionalFamilyPublisher.read_locks([config.support_output_dir.parent]):
        with acquisition_read_locks(raw_dir, source_manifest_path.parent):
            validate_hourly_product(source_manifest_path)
            source = load_manifest(source_manifest_path)
            settings = source["resolved_config"]
            weather = config.surface_weather
            if (settings["timezone"] != weather.timezone
                    or settings["h3_resolution"] != weather.h3_resolution
                    or settings["availability_lag_hours"] != weather.availability_lag_hours):
                raise ValueError("Hourly acquisition policy differs from the current build configuration.")
            support = load_meteorological_support(weather.h3_resolution, config.path)
            if (source["spatial_bounds_wgs84"] != config.bbox
                    or settings["support_cell_set_hash"] != cell_set_hash(support["H3_INDEX"])):
                raise ValueError("Hourly acquisition spatial bounds or support differ from the current build configuration.")
            base = source_manifest_path.parent
            inventory_item = source["artifacts"][0]
            inventory_path = resolve_portable_path(inventory_item["path"], base=base)
            pinned_support, pinned_support_manifest = snapshot_support_inputs(
                raw_dir=raw_dir, support_path=config.support_path(config.surface_weather.h3_resolution),
                support_manifest_path=config.support_manifest_path,
            )
            inventory_path, source_manifest_path = snapshot_acquisition_metadata(
                raw_dir=raw_dir, inventory_path=inventory_path, manifest_path=source_manifest_path,
                manifest=source, pinned_inputs={
                    config.support_path(config.surface_weather.h3_resolution).resolve(): pinned_support,
                    config.support_manifest_path.resolve(): pinned_support_manifest,
                },
            )
            source = load_manifest(source_manifest_path)
            base = source_manifest_path.parent
            inventory = pq.read_table(inventory_path).to_pandas()
            samples = {item["checksum"]: path for item, path in _inputs_by_role(
                source, "sample", base=base)}
            frames = [pq.read_table(samples[row.SAMPLE_CHECKSUM]).to_pandas()
                      for row in inventory.itertuples(index=False)]
    local_date = source["resolved_config"]["local_date"]
    full = pd.concat(frames, ignore_index=True).sort_values(
        ["DATE", "VALID_TIME_UTC", "H3_INDEX"], ignore_index=True)
    expected_cells = set(full["H3_INDEX"].astype(str))
    validate_hourly_records(_records(full), local_date=local_date,
                            timezone_name=source["resolved_config"]["timezone"],
                            expected_h3_cells=expected_cells,
                            availability_lag_hours=source["resolved_config"]["availability_lag_hours"],
                            expected_source_model=("synthetic_hrrr" if source["resolved_config"][
                                "source_evidence_kind"] == "synthetic_fixture" else "HRRR"))
    destination = processed / "H3_HOURLY_WEATHER_RES_5"
    manifest_path = processed / "MANIFEST.json"
    run_id = run_id or f"hourly-build-{uuid.uuid4().hex[:12]}"
    with TransactionalFamilyPublisher(processed, run_id=run_id) as publisher:
        staged = publisher.stage_path(destination)
        staged_manifest = publisher.stage_manifest_path(manifest_path)
        write_table(staged / f"date={local_date}" / "part-000.parquet",
                    pa.Table.from_pandas(full, preserve_index=False), HOURLY_WEATHER_SCHEMA)
        inputs = [*source["inputs"], dict(role="inventory", path=str(inventory_path),
                                          checksum=checksum_path(inventory_path)),
                  dict(role="acquisition_manifest", path=str(source_manifest_path),
                       checksum=checksum_path(source_manifest_path))]
        payload = manifest_payload(
            product=HOURLY_PRODUCT, run_id=run_id, config_path=config.path,
            resolved_config={**source["resolved_config"], "source_release_id": source["release_id"],
                             "hourly_schema_version": "hourly-weather-r5-v1"},
            artifacts=[parquet_contract(staged, published_path=destination)],
            inputs=inputs, sources=source["sources"],
            h3_resolution=config.surface_weather.h3_resolution,
            spatial_bounds=source["spatial_bounds_wgs84"],
            temporal_coverage=source["temporal_coverage"],
            availability_semantics=source["availability_semantics"],
            units=dict(temperature="degrees Celsius", humidity="percent", wind="metres per second",
                       visibility="kilometres", cloud="percent", pressure="hectopascals"),
            formulas=dict(WIND_SPEED_10M_MS="hypot(U_WIND_10M_MS, V_WIND_10M_MS)"),
            limitations=["Instantaneous f00 sampled values, not interpolated or repeated daily snapshots.",
                         "As-of availability is an assumed lag, not measured provider publication.",
                         "No precipitation amount or rate is included."],
        )
        write_manifest(staged_manifest, payload)
        publisher.publish()
    return manifest_path


def validate_hourly_product(manifest_path: str | Path) -> dict:
    """Deep-check hourly lineage, exact time × cell coverage and source identities."""
    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    product = manifest["product"]
    if product not in {ACQUISITION_PRODUCT, HOURLY_PRODUCT}:
        raise ValueError("Not an hourly-weather product.")
    from ..methods import method_version

    if manifest["method_version"] != method_version(product):
        raise ValueError("Hourly product method version is incompatible.")
    resolved = manifest["resolved_config"]
    if resolved.get("availability_policy") != AVAILABILITY_POLICY or resolved.get(
            "field_contract_version") != FIELD_CONTRACT_VERSION or resolved.get(
            "spatial_acceptance_policy") != SPATIAL_ACCEPTANCE_POLICY:
        raise ValueError("Hourly manifest scientific policies are incompatible.")
    if resolved.get("required_fields") != list(HOURLY_CORE) or resolved.get("optional_fields") != []:
        raise ValueError("Hourly manifest capability contract is incompatible.")
    local_date = resolved["local_date"]
    expected_times = hourly_utc_instants(local_date, resolved["timezone"])
    if manifest["temporal_coverage"].get("expected_hours") != len(expected_times):
        raise ValueError("Hourly manifest civil-day duration is incorrect.")
    base = path.parent
    supports = _inputs_by_role(manifest, "support", base=base)
    support_manifests = _inputs_by_role(manifest, "support_manifest", base=base)
    if len(support_manifests) != 1:
        raise ValueError("Hourly product lacks one support manifest.")
    support_manifest = load_manifest(support_manifests[0][1], verify_artifacts=False)
    support_settings = support_manifest["resolved_config"]
    if (support_manifest["product"] != "meteorological.spatial_support"
            or support_manifest["spatial_bounds_wgs84"] != manifest["spatial_bounds_wgs84"]
            or support_settings.get("bbox") != manifest["spatial_bounds_wgs84"]
            or support_settings.get("support_method") != SUPPORT_METHOD
            or resolved["h3_resolution"] not in support_settings.get("resolutions", [])):
        raise ValueError("Hourly spatial bounds differ from retained support.")
    if product == HOURLY_PRODUCT:
        acquisitions = _inputs_by_role(manifest, "acquisition_manifest", base=base)
        if len(acquisitions) != 1 or load_manifest(acquisitions[0][1], verify_artifacts=False)[
                "spatial_bounds_wgs84"] != manifest["spatial_bounds_wgs84"]:
            raise ValueError("Hourly spatial bounds differ from acquisition.")
    inventories = (_inputs_by_role(manifest, "inventory", base=base)
                   if product == HOURLY_PRODUCT else [(manifest["artifacts"][0],
                       resolve_portable_path(manifest["artifacts"][0]["path"], base=base))])
    if len(supports) != 1 or len(inventories) != 1:
        raise ValueError("Hourly product lacks one support or inventory input.")
    support = pq.read_table(supports[0][1]).to_pandas()
    support_cells = set(support["H3_INDEX"].astype(str))
    if not support_cells or cell_set_hash(support_cells) != resolved["support_cell_set_hash"]:
        raise ValueError("Hourly support cell identity differs from its manifest.")
    inventory_file = pq.ParquetFile(inventories[0][1])
    if not inventory_file.schema_arrow.equals(HOURLY_WEATHER_INVENTORY_SCHEMA, check_metadata=False):
        raise ValueError("Hourly inventory Arrow schema differs.")
    inventory = inventory_file.read().to_pandas()
    if (len(inventory) != len(expected_times) or set(inventory["VALID_TIME_UTC"].astype(str))
            != {stamp.isoformat() for stamp in expected_times}):
        raise ValueError("Hourly inventory misses or duplicates f00 times.")
    samples = {item["checksum"]: sample_path for item, sample_path in _inputs_by_role(
        manifest, "sample", base=base)}
    crosswalks = {item["checksum"]: crosswalk_path for item, crosswalk_path in _inputs_by_role(
        manifest, "crosswalk", base=base)}
    decoded_grids = {item["checksum"]: input_path for item, input_path in _inputs_by_role(
        manifest, "decoded_grid", base=base)}
    decoded_evidence = {item["valid_time_utc"]: input_path for item, input_path in _inputs_by_role(
        manifest, "decoded_evidence", base=base)}
    if len(_inputs_by_role(manifest, "sample", base=base)) != len(expected_times):
        raise ValueError("Hourly sample input count is incomplete.")
    frames: list[pd.DataFrame] = []
    for row in inventory.itertuples(index=False):
        if (row.SAMPLE_CHECKSUM not in samples or row.CROSSWALK_CHECKSUM not in crosswalks
                or row.DECODED_INPUT_CHECKSUM not in decoded_grids
                or row.VALID_TIME_UTC not in decoded_evidence
                or row.H3_CELL_COUNT != len(support_cells) or row.LOCAL_DATE != local_date):
            raise ValueError("Hourly inventory sample/crosswalk support or identity is incomplete.")
        sample_file = pq.ParquetFile(samples[row.SAMPLE_CHECKSUM])
        if not sample_file.schema_arrow.equals(HOURLY_WEATHER_SCHEMA, check_metadata=False):
            raise ValueError("Hourly sample Arrow schema differs.")
        frame = sample_file.read().to_pandas()
        validate_fields(frame, HOURLY_WEATHER_SCHEMA, context="Hourly sample")
        if (len(frame) != len(support_cells)
                or set(frame["H3_INDEX"].astype(str)) != support_cells
                or frame["H3_INDEX"].duplicated().any()
                or set(frame["VALID_TIME_UTC"].astype(str)) != {row.VALID_TIME_UTC}
                or set(frame["SOURCE_GRID_HASH"].astype(str)) != {row.SOURCE_GRID_HASH}
                or set(frame["SOURCE_OBJECT_URI"].astype(str)) != {row.SOURCE_OBJECT_URI}
                or set(frame["SOURCE_URI"].astype(str)) != {row.SOURCE_URI}
                or set(frame["SOURCE_EVIDENCE_KIND"].astype(str)) != {row.SOURCE_EVIDENCE_KIND}):
            raise ValueError("Hourly sample rows differ from inventory identity/support.")
        crosswalk = pq.read_table(crosswalks[row.CROSSWALK_CHECKSUM]).to_pandas()
        if (len(crosswalk) != len(support_cells) or set(crosswalk["H3_INDEX"].astype(str))
                != support_cells or crosswalk["H3_INDEX"].duplicated().any()
                or set(crosswalk["SOURCE_GRID_HASH"].astype(str)) != {row.SOURCE_GRID_HASH}):
            raise ValueError("Hourly crosswalk differs from inventory support/grid.")
        valid = _utc(row.VALID_TIME_UTC)
        source_grid, evidence = _load_decoded_grid(
            decoded_grids[row.DECODED_INPUT_CHECKSUM], decoded_evidence[row.VALID_TIME_UTC],
            valid=valid, lag_hours=resolved["availability_lag_hours"],
        )
        if evidence["source_evidence_kind"] != resolved["source_evidence_kind"]:
            raise ValueError("Hourly decoded source kind differs from its release policy.")
        expected_crosswalk = build_nearest_grid_crosswalk(support, source_grid)
        if not crosswalk[HRRR_CROSSWALK_SCHEMA.names].equals(
                expected_crosswalk[HRRR_CROSSWALK_SCHEMA.names]):
            raise ValueError("Hourly crosswalk differs from retained decoded native grid.")
        expected_sample = _sample(source_grid, crosswalk, evidence, local_date=local_date)
        if not frame[HOURLY_WEATHER_SCHEMA.names].equals(expected_sample):
            raise ValueError("Hourly sample differs from retained decoded native values.")
        joined = frame.merge(crosswalk, on=["H3_INDEX", "SOURCE_GRID_HASH"],
                             suffixes=("", "_MAP"), validate="one_to_one")
        if (len(joined) != len(support_cells)
                or not joined["SOURCE_GRID_INDEX"].eq(joined["SOURCE_GRID_INDEX_MAP"]).all()
                or not np.allclose(joined["SOURCE_GRID_DISTANCE_M"],
                                   joined["SOURCE_GRID_DISTANCE_M_MAP"], atol=1e-8, rtol=0)):
            raise ValueError("Hourly sample native-point mapping differs from crosswalk.")
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    validate_hourly_records(_records(combined), local_date=local_date,
                            timezone_name=resolved["timezone"], expected_h3_cells=support_cells,
                            availability_lag_hours=resolved["availability_lag_hours"],
                            expected_source_model=("synthetic_hrrr" if resolved[
                                "source_evidence_kind"] == "synthetic_fixture" else "HRRR"))
    if product == HOURLY_PRODUCT:
        artifact = resolve_portable_path(manifest["artifacts"][0]["path"], base=base)
        files = sorted(artifact.rglob("*.parquet"))
        if len(files) != 1 or not pq.read_schema(files[0]).equals(
                HOURLY_WEATHER_SCHEMA, check_metadata=False):
            raise ValueError("Published hourly Arrow schema/partition differs.")
        published = pq.read_table(files[0]).to_pandas()
        keys = ["H3_INDEX", "DATE", "VALID_TIME_UTC"]
        expected = combined.sort_values(keys, ignore_index=True)[HOURLY_WEATHER_SCHEMA.names]
        observed = published.sort_values(keys, ignore_index=True)[HOURLY_WEATHER_SCHEMA.names]
        if not expected.equals(observed):
            raise ValueError("Published hourly values differ from pinned acquisition samples.")
    return dict(valid=True, product=product, release_id=manifest["release_id"],
                expected_hours=len(expected_times), h3_cells=len(support_cells),
                row_count=len(combined), source_evidence_kind=resolved["source_evidence_kind"])
