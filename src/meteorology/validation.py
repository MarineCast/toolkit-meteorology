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
from .core.data import meteorological_schemas as schemas
from .methods import METHOD_VERSIONS


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
    "PRECIP_MM_DAY_ESTIMATE": (0.0, math.inf),
    "DAYLIGHT_HOURS": (0.0, 24.0),
    "DAYLIGHT_FRACTION": (0.0, 1.0),
    "LUNAR_ILLUMINATION_FRACTION": (0.0, 1.0),
    "MOONLIT_DARK_FRACTION": (0.0, 1.0),
}


def _validate_frame(
    frame: pd.DataFrame,
    *,
    product: str,
    schema: pa.Schema,
    resolution: int | None,
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
        raise ValueError(f"{product}: current method version is not recorded in this manifest.")
    seen_by_schema: dict[tuple[str, ...], set[tuple[str, ...]]] = defaultdict(set)
    dates_by_schema: dict[tuple[str, ...], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    artifact_count = 0
    total_rows = 0
    for declared in manifest["artifacts"]:
        path = resolve_portable_path(declared["path"], base=Path(manifest_path).parent)
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
                            seen=seen_by_schema[key], dates=dates_by_schema[key])
            total_rows += len(frame)
        artifact_count += 1
    start = manifest.get("temporal_coverage", {}).get("start_date")
    end = manifest.get("temporal_coverage", {}).get("end_date")
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
    if metadata.get("schema_version") != 1 or set(metadata.get("native_manifests", {})) != {"surface_weather", "daylight", "lunar"}:
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
    seen: set[tuple[str, str, int]] = set()
    dates: set[str] = set()
    counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
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
        for resolution, absent in ((4, ("surface_weather__", "lunar__")), (5, ("daylight__",))):
            subset = frame[frame["H3_RESOLUTION"] == resolution]
            columns = [name for name in subset if name.startswith(absent)]
            if columns and not subset[columns].isna().all().all():
                raise ValueError("Daily matrix has a value at an unsupported native resolution.")
        rows += len(frame)
    expected = set(pd.date_range(metadata["temporal_coverage"]["start_date"], metadata["temporal_coverage"]["end_date"]).strftime("%Y-%m-%d"))
    if dates != expected:
        raise ValueError("Daily matrix date coverage differs from embedded metadata.")
    for resolution in (4, 5):
        if set(counts[resolution]) != expected or len(set(counts[resolution].values())) != 1:
            raise ValueError(f"Daily matrix R{resolution} support count varies by date.")
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
