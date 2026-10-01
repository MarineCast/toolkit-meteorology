"""Read-only validation of published products and native daily-matrix exports."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import h3
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .artifacts import checksum_path, load_manifest, parquet_contract, parquet_files, resolve_portable_path, stable_hash
from .astronomy import local_civil_day_hours as _local_civil_day_hours
from .core.data import meteorological_schemas as schemas
from .methods import METHOD_VERSIONS
from .surface_weather.wind import validate_daily_wind_vectors
from .surface_weather.download import _expected_times, retrieval_matches_object, valid_retrieval_time
from .surface_weather.source import hrrr_logical_object_uri
from .surface_weather.storage import acquisition_lock, resolve_raw_relative


EXPECTED: dict[str, tuple[pa.Schema, ...]] = {
    "meteorological.spatial_support": (schemas.SUPPORT_SCHEMA,),
    "meteorological.surface_weather.download": (schemas.HRRR_INVENTORY_SCHEMA,),
    "meteorological.surface_weather": (schemas.SURFACE_WEATHER_DAILY_SCHEMA, schemas.HRRR_CROSSWALK_SCHEMA),
    "meteorological.daylight": (schemas.DAYLIGHT_SCHEMA, schemas.DAYLIGHT_DOY_SCHEMA),
    "meteorological.lunar": (schemas.LUNAR_SCHEMA,),
}

RANGES = {
    "TEMPERATURE_2M_C_MEAN": (-100.0, 70.0),
    "RELATIVE_HUMIDITY_2M_PCT_MEAN": (0.0, 100.0),
    "TOTAL_CLOUD_COVER_PCT_MEAN": (0.0, 100.0),
    "WIND_DIRECTION_FROM_10M_DEG": (0.0, 360.0),
    "WIND_SPEED_10M_MS_MEAN": (0.0, math.inf),
    "WIND_SPEED_10M_MS_MAX": (0.0, math.inf),
    "WIND_VECTOR_SPEED_10M_MS": (0.0, math.inf),
    "WIND_GUST_SURFACE_MS_MEAN": (0.0, math.inf),
    "WIND_GUST_SURFACE_MS_MAX": (0.0, math.inf),
    "VISIBILITY_KM_MEAN": (0.0, math.inf),
    "VISIBILITY_KM_MIN": (0.0, math.inf),
    "SOURCE_GRID_DISTANCE_M_MEAN": (0.0, math.inf),
    "SOURCE_GRID_DISTANCE_M_MAX": (0.0, math.inf),
    "PRECIP_MM_DAY_ESTIMATE": (0.0, math.inf),
    "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN": (800.0, 1100.0),
    "MEAN_SEA_LEVEL_PRESSURE_HPA_MIN": (800.0, 1100.0),
    "DAYLIGHT_HOURS": (0.0, 24.0),
    "DAYLIGHT_FRACTION": (0.0, 1.0),
    "SOLAR_ELEVATION_MAX_DEG": (-90.0, 90.0),
    "SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG": (0.0, 90.0),
    "LOW_SUN_DAYLIGHT_HOURS": (0.0, math.inf),
    "LUNAR_PHASE_ANGLE_DEG": (0.0, 360.0),
    "LUNAR_ILLUMINATION_FRACTION": (0.0, 1.0),
    "NIGHT_HOURS": (0.0, math.inf),
    "MOON_VISIBLE_HOURS": (0.0, math.inf),
    "MOON_VISIBLE_DARK_HOURS": (0.0, math.inf),
    "MOONLIT_DARK_HOURS": (0.0, math.inf),
    "MOON_VISIBLE_DARK_FRACTION": (0.0, 1.0),
    "MOONLIT_DARK_FRACTION": (0.0, 1.0),
}

INTEGRATED_HOUR_FIELDS = frozenset({
    "LOW_SUN_DAYLIGHT_HOURS", "NIGHT_HOURS", "MOON_VISIBLE_HOURS",
    "MOON_VISIBLE_DARK_HOURS", "MOONLIT_DARK_HOURS",
})


def _validate_frame(
    frame: pd.DataFrame,
    *,
    product: str,
    schema: pa.Schema,
    resolution: int | None,
    timezone: str | None,
    seen: set[tuple[str, ...]],
    dates: dict[str, set[str]],
) -> None:
    if frame.empty:
        raise ValueError(f"{product}: an artifact partition is empty.")
    required = [field.name for field in schema if not field.nullable]
    if frame[required].isna().any().any():
        raise ValueError(f"{product}: non-nullable fields contain null values.")
    numeric = frame.select_dtypes(include="number")
    if np.isinf(numeric.to_numpy(dtype=float)).any():
        raise ValueError(f"{product}: non-finite numeric value.")
    for column, (lower, upper) in RANGES.items():
        if column in frame:
            values = frame[column].dropna()
            if (values < lower).any() or (values >= upper if column == "WIND_DIRECTION_FROM_10M_DEG" else values > upper).any():
                raise ValueError(f"{product}: {column} is outside its declared range.")
    integrated = INTEGRATED_HOUR_FIELDS.intersection(frame.columns)
    if integrated:
        if not timezone or "DATE" not in frame:
            raise ValueError(f"{product}: integrated-hour validation requires local dates and timezone.")
        duration = frame["DATE"].astype(str).map(
            lambda date: _local_civil_day_hours(date, timezone)
        ).to_numpy(dtype=float)
        for column in integrated:
            values = frame[column].to_numpy(dtype=float)
            if (values > duration + 1e-8).any():
                raise ValueError(f"{product}: {column} exceeds its local civil-day duration.")
    if product == "meteorological.surface_weather" and "WIND_VECTOR_SPEED_10M_MS" in frame:
        validate_daily_wind_vectors(frame)
    if "H3_INDEX" in frame:
        cells = frame["H3_INDEX"].astype(str)
        for cell in cells.unique():
            if not h3.is_valid_cell(cell):
                raise ValueError(f"{product}: invalid H3 cell {cell!r}.")
            if resolution is not None and h3.get_resolution(cell) != resolution:
                raise ValueError(f"{product}: H3 resolution differs from manifest R{resolution}.")
    keys = [key for key in ("H3_INDEX", "DATE", "DAY_OF_YEAR", "SOURCE_GRID_HASH", "VALID_TIME_UTC") if key in frame]
    # Acquisition inventory is one row per valid time; support is one row per cell.
    if product == "meteorological.surface_weather.download":
        keys = ["VALID_TIME_UTC"]
    elif "DATE" in keys:
        keys = ["H3_INDEX", "DATE"]
    elif "DAY_OF_YEAR" in keys:
        keys = ["H3_INDEX", "DAY_OF_YEAR"]
    elif "SOURCE_GRID_HASH" in keys:
        keys = ["H3_INDEX", "SOURCE_GRID_HASH"]
    elif "H3_INDEX" in keys:
        keys = ["H3_INDEX"]
    if keys:
        for record in frame[keys].itertuples(index=False, name=None):
            identity = tuple(str(value) for value in record)
            if identity in seen:
                raise ValueError(f"{product}: duplicate primary key {identity}.")
            seen.add(identity)
    if "DATE" in frame:
        for date, group in frame.groupby("DATE"):
            try:
                if pd.Timestamp(date).strftime("%Y-%m-%d") != date:
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{product}: invalid local DATE {date!r}.") from exc
            dates[str(date)].update(group["H3_INDEX"].astype(str))
    if product == "meteorological.surface_weather" and "SAMPLE_COUNT" in frame:
        if not frame["SAMPLE_COUNT"].eq(frame["EXPECTED_SAMPLE_COUNT"]).all() or not frame["SAMPLE_COVERAGE_FRAC"].eq(1.0).all():
            raise ValueError("Surface-weather daily sample coverage is incomplete.")


def validate_product(manifest_path: str | Path) -> dict[str, Any]:
    """Validate manifest, checksums, schemas, primary keys, ranges and coverage."""

    path = Path(manifest_path)
    if path.name == "R5_DOWNLOAD_MANIFEST.json":
        with acquisition_lock(path.parent, writer=False):
            return _validate_product_unlocked(path)
    return _validate_product_unlocked(path)


def _validate_product_unlocked(manifest_path: str | Path) -> dict[str, Any]:
    """Validate a stable manifest and its declared artifacts."""

    manifest = load_manifest(manifest_path, verify_artifacts=True)
    archived = manifest.get("archived_hrrr_samples")
    if archived is not None:
        archived_path = resolve_portable_path(archived["path"], base=Path(manifest_path).parent)
        if checksum_path(archived_path) != archived["checksum"]:
            raise ValueError("Frozen HRRR samples have a checksum mismatch.")
    product = str(manifest["product"])
    if product not in EXPECTED:
        raise ValueError(f"Unsupported product for deep validation: {product}")
    if manifest.get("method_version") != METHOD_VERSIONS[product]:
        raise ValueError(f"{product}: archived method requires its archived deep validator.")
    seen_by_schema: dict[tuple[str, ...], set[tuple[str, ...]]] = defaultdict(set)
    dates_by_schema: dict[tuple[str, ...], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    artifact_count = 0
    total_rows = 0
    acquisition_frames: list[pd.DataFrame] = []
    acquisition_inventory_source: Path | None = None
    checked_crosswalks: dict[tuple[Path, str, str], set[str]] = {}
    for declared in manifest["artifacts"]:
        path = resolve_portable_path(declared["path"], base=Path(manifest_path).parent)
        if manifest["product"] == "meteorological.surface_weather.download":
            acquisition_inventory_source = path
        observed = parquet_contract(path, published_path=path)
        for field in ("checksum", "file_count", "row_count", "schema", "h3_cell_count", "h3_cell_set_hash"):
            if observed[field] != declared[field]:
                raise ValueError(f"{product}: artifact {field} differs from manifest: {path}")
        for file in parquet_files(path):
            parquet = pq.ParquetFile(file)
            schema = parquet.schema_arrow
            if not any(schema.equals(candidate, check_metadata=False) for candidate in EXPECTED[product]):
                raise ValueError(f"{product}: unexpected Arrow schema: {file}")
            key = tuple(schema.names)
            frame = parquet.read().to_pandas()
            resolution = manifest["h3_resolution"]
            if "H3_RESOLUTION" in frame:
                if frame["H3_RESOLUTION"].nunique() != 1:
                    raise ValueError(f"{product}: mixed H3 resolutions in {file}")
                resolution = int(frame["H3_RESOLUTION"].iloc[0])
            _validate_frame(frame, product=product, schema=schema, resolution=resolution,
                            timezone=manifest.get("resolved_config", {}).get("timezone"),
                            seen=seen_by_schema[key], dates=dates_by_schema[key])
            if product == "meteorological.surface_weather.download":
                acquisition_frames.append(frame)
            total_rows += len(frame)
        artifact_count += 1
    start = manifest.get("temporal_coverage", {}).get("start_date")
    end = manifest.get("temporal_coverage", {}).get("end_date")
    if product == "meteorological.surface_weather.download":
        resolved = manifest["resolved_config"]
        if not start or not end:
            raise ValueError("Acquisition manifest lacks its declared local-date interval.")
        inventory = pd.concat(acquisition_frames, ignore_index=True)
        expected = {
            value.tz_convert("UTC").isoformat()
            for value in _expected_times(start, end, resolved["timezone"], resolved["interval_hours"])
        }
        if len(inventory) != len(expected) or set(inventory["VALID_TIME_UTC"].astype(str)) != expected:
            raise ValueError("Acquisition inventory does not cover its exact continuous schedule.")
        if set(inventory["STATUS"].astype(str)) != {"COMPLETE"}:
            raise ValueError("Acquisition inventory contains incomplete cycles.")
        spatial = resolved.get("spatial_acceptance")
        if not isinstance(spatial, dict) or not spatial.get("policy_id") or not spatial.get("support_hash"):
            raise ValueError("Acquisition manifest lacks the spatial acceptance policy.")
        synthetic = resolved["source"].get("model") == "synthetic_hrrr"
        if acquisition_inventory_source is None:
            raise ValueError("Acquisition inventory artifact is missing.")
        raw_root = acquisition_inventory_source.parent
        for row in inventory.itertuples(index=False):
            valid = pd.Timestamp(row.VALID_TIME_UTC)
            invalid = row.AVAILABLE_AT_UTC != (
                valid + pd.Timedelta(hours=resolved["availability_lag_hours"])
            ).isoformat()
            if synthetic:
                invalid |= (
                    row.SOURCE_BACKEND != "synthetic_fixture"
                    or not str(row.SOURCE_URI).startswith("synthetic://")
                    or not str(row.PRECIP_SOURCE_URI).startswith("synthetic://")
                    or pd.notna(row.SOURCE_OBJECT_URI)
                    or pd.notna(row.PRECIP_OBJECT_URI)
                )
            else:
                source_object = hrrr_logical_object_uri(valid)
                precip_object = hrrr_logical_object_uri(
                    valid, resolved["source"]["precipitation_forecast_hour"]
                )
                invalid |= (
                    row.SOURCE_OBJECT_URI != source_object
                    or row.PRECIP_OBJECT_URI != precip_object
                    or not retrieval_matches_object(row.SOURCE_URI, source_object)
                    or not retrieval_matches_object(row.PRECIP_SOURCE_URI, precip_object)
                    or not valid_retrieval_time(row.SOURCE_RETRIEVED_AT_UTC)
                    or not valid_retrieval_time(row.PRECIP_RETRIEVED_AT_UTC)
                )
            if invalid:
                raise ValueError(f"Acquisition inventory row violates release provenance: {valid}.")
            if (
                row.SPATIAL_POLICY_ID != spatial["policy_id"]
                or row.SUPPORT_HASH != spatial["support_hash"]
                or row.NATIVE_GRID_CHECKSUM not in spatial["native_grid_checksums"]
            ):
                raise ValueError(f"Acquisition inventory row violates spatial acceptance identity: {valid}.")
            sample = resolve_raw_relative(raw_root, str(row.RELATIVE_PATH))
            crosswalk = resolve_raw_relative(raw_root, str(row.CROSSWALK_RELATIVE_PATH))
            if not sample.is_file() or checksum_path(sample) != str(row.CHECKSUM):
                raise ValueError(f"Acquisition sample is missing or checksum-invalid: {sample}")
            sample_file = pq.ParquetFile(sample)
            if not sample_file.schema_arrow.equals(schemas.HRRR_SAMPLE_SCHEMA, check_metadata=False):
                raise ValueError(f"Acquisition sample schema is invalid: {sample}")
            sample_frame = sample_file.read().to_pandas()
            if (
                len(sample_frame) != int(row.H3_CELL_COUNT)
                or sample_frame["H3_INDEX"].duplicated().any()
                or set(sample_frame["VALID_TIME_UTC"].astype(str)) != {valid.isoformat()}
                or set(sample_frame["AVAILABLE_AT_UTC"].astype(str)) != {str(row.AVAILABLE_AT_UTC)}
                or set(sample_frame["SOURCE_GRID_HASH"].astype(str)) != {str(row.SOURCE_GRID_HASH)}
            ):
                raise ValueError(f"Acquisition sample identity is invalid: {sample}")
            crosswalk_key = (crosswalk, str(row.CROSSWALK_CHECKSUM), str(row.SOURCE_GRID_HASH))
            if crosswalk_key not in checked_crosswalks:
                if not crosswalk.is_file() or checksum_path(crosswalk) != str(row.CROSSWALK_CHECKSUM):
                    raise ValueError(f"Acquisition crosswalk is missing or checksum-invalid: {crosswalk}")
                crosswalk_file = pq.ParquetFile(crosswalk)
                if not crosswalk_file.schema_arrow.equals(schemas.HRRR_CROSSWALK_SCHEMA, check_metadata=False):
                    raise ValueError(f"Acquisition crosswalk schema is invalid: {crosswalk}")
                crosswalk_frame = crosswalk_file.read().to_pandas()
                if (
                    len(crosswalk_frame) != int(row.H3_CELL_COUNT)
                    or crosswalk_frame["H3_INDEX"].duplicated().any()
                    or set(crosswalk_frame["SOURCE_GRID_HASH"].astype(str)) != {str(row.SOURCE_GRID_HASH)}
                    or set(crosswalk_frame["SPATIAL_POLICY_ID"].astype(str)) != {str(row.SPATIAL_POLICY_ID)}
                    or set(crosswalk_frame["NATIVE_GRID_CHECKSUM"].astype(str)) != {str(row.NATIVE_GRID_CHECKSUM)}
                    or set(crosswalk_frame["SUPPORT_HASH"].astype(str)) != {str(row.SUPPORT_HASH)}
                ):
                    raise ValueError(f"Acquisition crosswalk identity is invalid: {crosswalk}")
                checked_crosswalks[crosswalk_key] = set(crosswalk_frame["H3_INDEX"].astype(str))
            if set(sample_frame["H3_INDEX"].astype(str)) != checked_crosswalks[crosswalk_key]:
                raise ValueError(f"Acquisition sample and crosswalk support differ: {sample}")
    if start and end and product != "meteorological.surface_weather.download":
        expected_dates = set(pd.date_range(start, end).strftime("%Y-%m-%d"))
        for key, dates in dates_by_schema.items():
            if "DATE" not in key:
                continue
            if set(dates) != expected_dates:
                raise ValueError(f"{product}: local-date coverage differs from manifest.")
            support = next(iter(dates.values()))
            if any(cells != support for cells in dates.values()):
                raise ValueError(f"{product}: H3 support differs between local dates.")
    return {
        "valid": True,
        "product": product,
        "release_id": manifest["release_id"],
        "method_version": manifest["method_version"],
        "artifact_count": artifact_count,
        "row_count": total_rows,
        "manifest_checksum": checksum_path(manifest_path),
    }


def validate_daily_matrix(path: str | Path) -> dict[str, Any]:
    """Validate the native-resolution combined table and embedded source lineage."""

    parquet = pq.ParquetFile(path)
    raw = (parquet.schema_arrow.metadata or {}).get(b"meteorology_daily_matrix")
    if raw is None:
        raise ValueError("Daily matrix lacks meteorology_daily_matrix metadata.")
    metadata = json.loads(raw)
    native_schemas = {
        "surface_weather": (schemas.SURFACE_WEATHER_DAILY_SCHEMA, 5),
        "daylight": (schemas.DAYLIGHT_SCHEMA, 4),
        "lunar": (schemas.LUNAR_SCHEMA, 5),
    }
    if metadata.get("schema_version") != 2 or set(metadata.get("native_manifests", {})) != set(native_schemas):
        raise ValueError("Daily matrix has incompatible schema or source manifests.")
    if metadata.get("method_version") != METHOD_VERSIONS["meteorological.daily_matrix"]:
        raise ValueError("Daily matrix has an incompatible scientific method version.")
    if not isinstance(metadata.get("software_version"), str) or not metadata["software_version"]:
        raise ValueError("Daily matrix lacks a software version.")
    identity = {
        "method": metadata["method_version"],
        "software": metadata["software_version"],
        "source_releases": {
            name: value["manifest"].get("release_id", value["checksum"])
            for name, value in metadata["native_manifests"].items()
        },
        "timezone": metadata["timezone"],
        "coverage": metadata["temporal_coverage"],
    }
    if metadata.get("release_id") != stable_hash(identity):
        raise ValueError("Daily matrix release ID differs from embedded source identity.")
    expected_fields = {
        "DATE": pa.string(), "H3_INDEX": pa.string(), "H3_RESOLUTION": pa.int8()
    }
    for component, (native_schema, _) in native_schemas.items():
        if metadata["native_manifests"][component]["manifest"].get("product") != f"meteorological.{component}":
            raise ValueError("Daily matrix declares an incompatible native product.")
        expected_fields.update({
            f"{component}__{field.name}": field.type
            for field in native_schema if field.name not in {"DATE", "H3_INDEX", "H3_RESOLUTION"}
        })
    actual_fields = {field.name: field.type for field in parquet.schema_arrow}
    if actual_fields != expected_fields or set(metadata.get("fields", {})) != set(expected_fields).difference({"DATE", "H3_INDEX", "H3_RESOLUTION"}):
        raise ValueError("Daily matrix scientific Arrow schema or field metadata differs from the native contracts.")
    sidecar = Path(path).with_suffix(Path(path).suffix + ".manifest.json")
    if not sidecar.exists():
        raise ValueError("Daily matrix content manifest is missing.")
    content = json.loads(sidecar.read_text(encoding="utf-8"))
    if content.get("release_id") != metadata["release_id"] or content.get("matrix_checksum") != checksum_path(path):
        raise ValueError("Daily matrix content checksum or release identity differs from its manifest.")
    seen: set[tuple[str, str, int]] = set()
    dates: set[str] = set()
    counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    support_sets: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    native_seen: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    native_dates: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    rows = 0
    for index in range(parquet.num_row_groups):
        frame = parquet.read_row_group(index).to_pandas()
        for record in frame[["DATE", "H3_INDEX", "H3_RESOLUTION"]].itertuples(index=False, name=None):
            date, cell, resolution = str(record[0]), str(record[1]), int(record[2])
            if not h3.is_valid_cell(cell) or h3.get_resolution(cell) != resolution or resolution not in {4, 5}:
                raise ValueError("Daily matrix has invalid H3 identity or resolution.")
            identity = (date, cell, resolution)
            if identity in seen:
                raise ValueError("Daily matrix has duplicate date/H3/resolution keys.")
            seen.add(identity)
            dates.add(date)
            counts[resolution][date] += 1
            support_sets[resolution][date].add(cell)
        for resolution, absent in ((4, ("surface_weather__", "lunar__")), (5, ("daylight__",))):
            subset = frame[frame["H3_RESOLUTION"] == resolution]
            columns = [name for name in subset if name.startswith(absent)]
            if columns and not subset[columns].isna().all().all():
                raise ValueError("Daily matrix has a value at an unsupported native resolution.")
        for component, (native_schema, resolution) in native_schemas.items():
            subset = frame[frame["H3_RESOLUTION"] == resolution]
            if subset.empty:
                continue
            native = subset[["DATE", "H3_INDEX", *(
                f"{component}__{field.name}" for field in native_schema
                if field.name not in {"DATE", "H3_INDEX", "H3_RESOLUTION"}
            )]].rename(columns=lambda name: name.removeprefix(f"{component}__"))
            _validate_frame(
                native, product=f"meteorological.{component}", schema=native_schema,
                resolution=resolution, timezone=metadata["timezone"],
                seen=native_seen[component], dates=native_dates[component]
            )
            if component == "surface_weather":
                validate_daily_wind_vectors(native)
            elif component == "daylight":
                absent = native["SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG"].isna().to_numpy()
                no_sampled_sun = native["SOLAR_ELEVATION_MAX_DEG"].to_numpy(dtype=float) <= 0.0
                if not np.array_equal(absent, no_sampled_sun):
                    raise ValueError("Daily matrix daylight mean nulls do not match sampled sunlight.")
            elif component == "lunar":
                no_night = native["NIGHT_HOURS"].to_numpy(dtype=float) == 0.0
                for name in ("MOON_VISIBLE_DARK_FRACTION", "MOONLIT_DARK_FRACTION", "WEIGHT_MOONLIT_DARK_HOURS"):
                    if not np.array_equal(no_night, native[name].isna().to_numpy()):
                        raise ValueError("Daily matrix lunar nulls do not match night support.")
        rows += len(frame)
    expected = set(pd.date_range(metadata["temporal_coverage"]["start_date"], metadata["temporal_coverage"]["end_date"]).strftime("%Y-%m-%d"))
    if dates != expected:
        raise ValueError("Daily matrix date coverage differs from embedded metadata.")
    for resolution in (4, 5):
        if set(counts[resolution]) != expected or len(set(counts[resolution].values())) != 1:
            raise ValueError(f"Daily matrix R{resolution} support count varies by date.")
        if len({frozenset(cells) for cells in support_sets[resolution].values()}) != 1:
            raise ValueError(f"Daily matrix R{resolution} support membership varies by date.")
    return {"valid": True, "product": "meteorological.daily_matrix", "row_count": rows,
            "date_count": len(dates), "h3_cell_counts": {str(k): next(iter(v.values())) for k, v in counts.items()},
            "checksum": checksum_path(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--manifest", type=Path, help="Validate a producer family manifest and its tables.")
    target.add_argument("--daily-matrix", type=Path, help="Validate a combined daily-matrix Parquet file.")
    parser.add_argument("--json-output", type=Path, help="Write the result as JSON.")
    args = parser.parse_args()
    try:
        result = validate_product(args.manifest) if args.manifest else validate_daily_matrix(args.daily_matrix)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        result = {"valid": False, "error": str(exc)}
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
