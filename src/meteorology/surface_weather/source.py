"""HRRR source acquisition, strict variable matching, and unit normalization."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pyarrow as pa

from .source_validation import (
    BoundingBox,
    _lat_lon_for_values,
    dataset_to_flat_variable_grid,
    infer_required_hrrr_variables,
    validate_grid_coverage,
    validate_decoded_source_times,
)

HRRR_MODEL = "hrrr"
HRRR_PRODUCT = "sfc"
FORECAST_HOUR = 0
PRECIP_FORECAST_HOUR = 1

HRRR_VARIABLES = {
    "temperature_2m_k": ":TMP:2 m above ground:",
    "relative_humidity_2m_pct": ":RH:2 m above ground:",
    "u_wind_10m_ms": ":UGRD:10 m above ground:",
    "v_wind_10m_ms": ":VGRD:10 m above ground:",
    "wind_gust_surface_ms": ":GUST:surface:",
    "visibility_m": ":VIS:surface:",
    "total_cloud_cover_pct": ":TCDC:entire atmosphere:",
    "precip_rate_kg_m2_s": ":PRATE:surface:",
    "mean_sea_level_pressure_pa": ":MSLMA:mean sea level:",
}

RAW_COLUMNS = [
    "SOURCE_GRID_INDEX",
    "SOURCE_LAT",
    "SOURCE_LON",
    "VALID_TIME_UTC",
    "INIT_TIME_UTC",
    "AVAILABLE_AT_UTC",
    "SOURCE_MODEL",
    "SOURCE_PRODUCT",
    "FORECAST_HOUR",
    "SOURCE_GRID_HASH",
    "SOURCE_WIND_BASIS",
    "TEMPERATURE_2M_K",
    "RELATIVE_HUMIDITY_2M_PCT",
    "U_WIND_10M_MS",
    "V_WIND_10M_MS",
    "WIND_GUST_SURFACE_MS",
    "VISIBILITY_M",
    "TOTAL_CLOUD_COVER_PCT",
    "PRECIP_RATE_KG_M2_S",
    "MEAN_SEA_LEVEL_PRESSURE_PA",
]

RAW_SCHEMA = pa.schema(
    [
        pa.field("SOURCE_GRID_INDEX", pa.int32(), nullable=False),
        pa.field("SOURCE_LAT", pa.float64(), nullable=False),
        pa.field("SOURCE_LON", pa.float64(), nullable=False),
        pa.field("VALID_TIME_UTC", pa.string(), nullable=False),
        pa.field("INIT_TIME_UTC", pa.string(), nullable=False),
        pa.field("AVAILABLE_AT_UTC", pa.string(), nullable=False),
        pa.field("SOURCE_MODEL", pa.string(), nullable=False),
        pa.field("SOURCE_PRODUCT", pa.string(), nullable=False),
        pa.field("FORECAST_HOUR", pa.int16(), nullable=False),
        pa.field("SOURCE_GRID_HASH", pa.string(), nullable=False),
        pa.field("SOURCE_WIND_BASIS", pa.string(), nullable=False),
        *[
            pa.field(name, pa.float64(), nullable=False)
            for name in RAW_COLUMNS
            if name
            not in {
                "SOURCE_GRID_INDEX",
                "SOURCE_LAT",
                "SOURCE_LON",
                "VALID_TIME_UTC",
                "INIT_TIME_UTC",
                "AVAILABLE_AT_UTC",
                "SOURCE_MODEL",
                "SOURCE_PRODUCT",
                "FORECAST_HOUR",
                "SOURCE_GRID_HASH",
                "SOURCE_WIND_BASIS",
            }
        ],
    ]
)

RAW_VARIABLE_COLUMNS = {
    "temperature_2m_k": "TEMPERATURE_2M_K",
    "relative_humidity_2m_pct": "RELATIVE_HUMIDITY_2M_PCT",
    "u_wind_10m_ms": "U_WIND_10M_MS",
    "v_wind_10m_ms": "V_WIND_10M_MS",
    "wind_gust_surface_ms": "WIND_GUST_SURFACE_MS",
    "visibility_m": "VISIBILITY_M",
    "total_cloud_cover_pct": "TOTAL_CLOUD_COVER_PCT",
    "precip_rate_kg_m2_s": "PRECIP_RATE_KG_M2_S",
    "mean_sea_level_pressure_pa": "MEAN_SEA_LEVEL_PRESSURE_PA",
}

# The hourly atmospheric product does not consume precipitation. Keep the legacy
# raw schema unchanged for the six-snapshot daily producer.
HOURLY_RAW_SCHEMA = pa.schema(
    [field for field in RAW_SCHEMA if field.name != "PRECIP_RATE_KG_M2_S"],
    metadata={b"meteorology_schema_version": b"hourly-decoded-atmosphere-v1"},
)


def _unit_text(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def normalize_hrrr_values(
    values: pd.Series,
    *,
    variable: str,
    units: str,
) -> pd.Series:
    """Validate source units and normalize to the canonical unit for one variable."""

    numeric = pd.to_numeric(values, errors="coerce").astype("float64")
    unit = _unit_text(units)
    if variable == "temperature_2m_k":
        if unit in {"k", "kelvin"}:
            return numeric
        if unit in {"c", "degc", "°c", "celsius"}:
            return numeric + 273.15
    elif variable in {"relative_humidity_2m_pct", "total_cloud_cover_pct"}:
        if unit in {"%", "percent", "pct"}:
            return numeric
        if unit in {"1", "fraction", "proportion"}:
            return numeric * 100.0
    elif variable in {"u_wind_10m_ms", "v_wind_10m_ms", "wind_gust_surface_ms"}:
        if unit in {"m/s", "ms-1", "ms**-1", "m.s-1"}:
            return numeric
    elif variable == "visibility_m":
        if unit in {"m", "meter", "metre", "meters", "metres"}:
            return numeric
        if unit in {"km", "kilometer", "kilometre", "kilometers", "kilometres"}:
            return numeric * 1000.0
    elif variable == "precip_rate_kg_m2_s":
        if unit in {
            "kgm-2s-1",
            "kgm**-2s**-1",
            "kgm^-2s^-1",
            "mm/s",
        }:
            return numeric
        if unit in {"mm/h", "mmhr-1", "mmhour-1"}:
            return numeric / 3600.0
    elif variable == "mean_sea_level_pressure_pa":
        if unit in {"pa", "pascal", "pascals"}:
            return numeric
        if unit in {"hpa", "mb", "mbar"}:
            return numeric * 100.0
    raise ValueError(f"Unsupported HRRR units for {variable}: {units!r}")


def _combined_search(variables: Mapping[str, str] = HRRR_VARIABLES) -> str:
    values = [value.strip(":") for value in variables.values()]
    return ":(?:" + "|".join(values) + ")"


def _decoded_wind_basis(mapping: Mapping[str, tuple[Any, str] | None]) -> str | None:
    names = ("u_wind_10m_ms", "v_wind_10m_ms")
    if not any(name in mapping for name in names):
        return None
    flags = []
    for name in names:
        selected = mapping.get(name)
        if selected is None:
            raise ValueError("HRRR wind components must be decoded as a pair.")
        dataset, variable = selected
        flag = dataset[variable].attrs.get("GRIB_uvRelativeToGrid")
        if str(flag) not in {"0", "1"}:
            raise ValueError(f"HRRR {name} has no usable GRIB wind-reference flag.")
        flags.append(int(flag))
    if flags[0] != flags[1]:
        raise ValueError("HRRR U/V wind-reference flags disagree.")
    return "grid_relative" if flags[0] else "earth_relative"


def hrrr_aws_archive_uri(valid_time_utc: pd.Timestamp, forecast_hour: int = FORECAST_HOUR) -> str:
    """Return the authoritative NOAA AWS HRRR object URI valid at one time."""

    valid = pd.Timestamp(valid_time_utc)
    valid = valid.tz_localize("UTC") if valid.tzinfo is None else valid.tz_convert("UTC")
    hour = int(forecast_hour)
    if hour < 0:
        raise ValueError("HRRR forecast_hour must be non-negative.")
    init = valid - pd.Timedelta(hours=hour)
    return (
        "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/"
        f"hrrr.{init:%Y%m%d}/conus/hrrr.t{init:%H}z.wrfsfcf{hour:02d}.grib2"
    )


def hrrr_logical_object_uri(
    valid_time_utc: pd.Timestamp, forecast_hour: int = FORECAST_HOUR
) -> str:
    """Name the NOAA cycle/product/lead independently of its retrieval host."""

    from urllib.parse import urlsplit

    return "noaa-hrrr://archive" + urlsplit(
        hrrr_aws_archive_uri(valid_time_utc, forecast_hour)
    ).path


def _grid_hash(latitudes: pd.Series, longitudes: pd.Series) -> str:
    from ..artifacts import stable_hash

    pairs = [
        (round(float(lat), 7), round(float(lon), 7))
        for lat, lon in zip(latitudes, longitudes, strict=True)
    ]
    return stable_hash(pairs)


def normalize_flat_grid(
    flat: pd.DataFrame,
    *,
    units_by_variable: Mapping[str, str],
    valid_time_utc: pd.Timestamp,
    availability_lag_hours: int,
    include_precipitation: bool = True,
) -> pd.DataFrame:
    """Normalize an identified HRRR grid independent of its storage format."""

    if flat.empty:
        raise ValueError("HRRR cropped source grid is empty.")
    variables = {name: column for name, column in RAW_VARIABLE_COLUMNS.items()
                 if include_precipitation or name != "precip_rate_kg_m2_s"}
    schema = RAW_SCHEMA if include_precipitation else HOURLY_RAW_SCHEMA
    missing = sorted(set(variables).difference(flat.columns))
    if missing:
        raise ValueError(f"HRRR cropped source grid is missing variables: {missing}")
    output = pd.DataFrame(
        {
            "SOURCE_GRID_INDEX": np.arange(len(flat), dtype="int32"),
            "SOURCE_LAT": pd.to_numeric(flat["hrrr_lat"], errors="raise").astype("float64"),
            "SOURCE_LON": pd.to_numeric(flat["hrrr_lon"], errors="raise").astype("float64"),
        }
    )
    missing_units = sorted(set(variables).difference(units_by_variable))
    if missing_units:
        raise ValueError(f"HRRR source grid is missing unit metadata: {missing_units}")
    for source_name, output_name in variables.items():
        output[output_name] = normalize_hrrr_values(
            flat[source_name], variable=source_name, units=str(units_by_variable[source_name])
        )
    valid = pd.Timestamp(valid_time_utc)
    valid = valid.tz_localize("UTC") if valid.tzinfo is None else valid.tz_convert("UTC")
    init = valid - pd.Timedelta(hours=FORECAST_HOUR)
    available = valid + pd.Timedelta(hours=int(availability_lag_hours))
    grid_hash = _grid_hash(output["SOURCE_LAT"], output["SOURCE_LON"])
    source_wind_basis = flat.attrs.get("source_wind_basis")
    if source_wind_basis not in {"grid_relative", "earth_relative"}:
        raise ValueError("HRRR source wind basis is missing after decoding.")
    output.insert(3, "VALID_TIME_UTC", valid.isoformat())
    output.insert(4, "INIT_TIME_UTC", init.isoformat())
    output.insert(5, "AVAILABLE_AT_UTC", available.isoformat())
    output.insert(6, "SOURCE_MODEL", HRRR_MODEL)
    output.insert(7, "SOURCE_PRODUCT", HRRR_PRODUCT)
    output.insert(8, "FORECAST_HOUR", FORECAST_HOUR)
    output.insert(9, "SOURCE_GRID_HASH", grid_hash)
    output.insert(10, "SOURCE_WIND_BASIS", source_wind_basis)
    if output[schema.names].isna().any().any():
        null_columns = output.columns[output.isna().any()].tolist()
        raise ValueError(f"HRRR raw source grid contains null values: {null_columns}")
    result = output[schema.names]
    result.attrs.update(flat.attrs)
    return result


def fetch_cropped_hrrr_fields(
    *,
    valid_time_utc: pd.Timestamp,
    bbox: Mapping[str, float],
    bbox_padding_degrees: float,
    availability_lag_hours: int,
    variables: Mapping[str, str] = HRRR_VARIABLES,
    forecast_hour: int = FORECAST_HOUR,
) -> tuple[pd.DataFrame, dict[str, str], str]:
    """Fetch identified HRRR fields without imposing the complete raw schema."""

    requested = dict(variables)
    unknown = sorted(set(requested).difference(HRRR_VARIABLES))
    if unknown or not requested:
        raise ValueError(f"Unknown or empty HRRR field request: {unknown}")

    try:
        from herbie import Herbie  # type: ignore
    except Exception as exc:
        raise ImportError("Install herbie-data to download HRRR data.") from exc
    valid = pd.Timestamp(valid_time_utc)
    valid = valid.tz_localize("UTC") if valid.tzinfo is None else valid.tz_convert("UTC")
    hour = int(forecast_hour)
    if hour < 0:
        raise ValueError("HRRR forecast_hour must be non-negative.")
    init = valid.tz_localize(None) - pd.Timedelta(hours=hour)
    show_logs = os.environ.get("METEOROLOGY_WEATHER_LOGS") == "1"
    with tempfile.TemporaryDirectory(prefix="meteorology_hrrr_") as temporary:
        source = Herbie(
            init,
            model=HRRR_MODEL,
            product=HRRR_PRODUCT,
            fxx=hour,
            save_dir=Path(temporary),
            verbose=show_logs,
        )
        dataset = source.xarray(_combined_search(requested), remove_grib=True)
        flat, units_by_variable = decode_hrrr_fields(
            dataset, valid_time_utc=valid, bbox=bbox,
            bbox_padding_degrees=bbox_padding_degrees, variables=requested,
            forecast_hour=hour,
        )
        source_name = str(getattr(source, "grib_source", None) or "")
        sources = getattr(source, "SOURCES", {}) or {}
        uri = str(
            sources.get(source_name) or getattr(source, "grib", None) or source_name or "unknown"
        )
    return flat, units_by_variable, uri


def decode_hrrr_fields(dataset: Any, *, valid_time_utc: pd.Timestamp,
                       bbox: Mapping[str, float], bbox_padding_degrees: float,
                       variables: Mapping[str, str], forecast_hour: int = 0
                       ) -> tuple[pd.DataFrame, dict[str, str]]:
    """Apply the same identity, wind and spatial gates to an already decoded source."""
    valid = pd.Timestamp(valid_time_utc)
    requested, hour = dict(variables), forecast_hour
    mapping = infer_required_hrrr_variables(
        dataset,
        requested,
        allow_missing=False,
        reject_ambiguous=True,
    )
    validate_decoded_source_times(mapping, valid_time_utc=valid, forecast_hour=hour)
    padded = BoundingBox(
        min_lat=float(bbox["min_lat"]) - float(bbox_padding_degrees),
        max_lat=float(bbox["max_lat"]) + float(bbox_padding_degrees),
        min_lon=float(bbox["min_lon"]) - float(bbox_padding_degrees),
        max_lon=float(bbox["max_lon"]) + float(bbox_padding_degrees),
    )
    wind_basis = _decoded_wind_basis(mapping)
    flat = dataset_to_flat_variable_grid(
        dataset, mapping, padded, pad_deg=0.0, wind_basis=wind_basis
    )
    first_dataset, first_variable = next(iter(mapping.values()))
    first_values = first_dataset[first_variable].squeeze(drop=True).values
    native_lat, native_lon = _lat_lon_for_values(first_dataset, first_values.shape)
    requested_bounds = BoundingBox(
        min_lat=float(bbox["min_lat"]), max_lat=float(bbox["max_lat"]),
        min_lon=float(bbox["min_lon"]), max_lon=float(bbox["max_lon"]),
    )
    coverage = validate_grid_coverage(
        native_lat, native_lon, flat, requested_bounds
    )
    flat.attrs["max_nearest_distance_m"] = coverage.max_nearest_distance_m
    flat.attrs["native_footprint"] = coverage.native_footprint
    if wind_basis is not None:
        flat.attrs["source_wind_basis"] = wind_basis
    units_by_variable: dict[str, str] = {}
    for output_name, mapping_value in mapping.items():
        if mapping_value is None:
            raise ValueError(f"Required HRRR variable was not mapped: {output_name}")
        variable_dataset, variable_name = mapping_value
        attrs = getattr(variable_dataset[variable_name], "attrs", {}) or {}
        units_by_variable[output_name] = str(attrs.get("units", attrs.get("GRIB_units")) or "")
    return flat, units_by_variable


def normalize_forecast_precip_grid(
    flat: pd.DataFrame,
    *,
    units: str,
    valid_time_utc: pd.Timestamp,
    forecast_hour: int = PRECIP_FORECAST_HOUR,
) -> pd.DataFrame:
    """Normalize one forecast PRATE grid without mixing it into f00 provenance."""

    if flat.empty:
        raise ValueError("HRRR forecast precipitation grid is empty.")
    required = {"hrrr_lat", "hrrr_lon", "precip_rate_kg_m2_s"}
    if missing := sorted(required.difference(flat.columns)):
        raise ValueError(f"HRRR forecast precipitation grid is missing columns: {missing}")
    valid = pd.Timestamp(valid_time_utc)
    valid = valid.tz_localize("UTC") if valid.tzinfo is None else valid.tz_convert("UTC")
    hour = int(forecast_hour)
    init = valid - pd.Timedelta(hours=hour)
    output = pd.DataFrame(
        {
            "SOURCE_GRID_INDEX": np.arange(len(flat), dtype="int32"),
            "SOURCE_LAT": pd.to_numeric(flat["hrrr_lat"], errors="raise").astype("float64"),
            "SOURCE_LON": pd.to_numeric(flat["hrrr_lon"], errors="raise").astype("float64"),
            "PRECIP_RATE_KG_M2_S": normalize_hrrr_values(
                flat["precip_rate_kg_m2_s"],
                variable="precip_rate_kg_m2_s",
                units=units,
            ),
        }
    )
    output["SOURCE_GRID_HASH"] = _grid_hash(output["SOURCE_LAT"], output["SOURCE_LON"])
    output["PRECIP_INIT_TIME_UTC"] = init.isoformat()
    output["PRECIP_VALID_TIME_UTC"] = valid.isoformat()
    output["PRECIP_FORECAST_HOUR"] = hour
    numeric = output.select_dtypes(include=[np.number]).to_numpy(dtype=float)
    if output.isna().any().any() or not np.isfinite(numeric).all():
        raise ValueError("HRRR forecast precipitation grid contains missing values.")
    return output


def fetch_cropped_hrrr_precip_grid(
    *,
    valid_time_utc: pd.Timestamp,
    bbox: Mapping[str, float],
    bbox_padding_degrees: float,
    availability_lag_hours: int,
    forecast_hour: int = PRECIP_FORECAST_HOUR,
) -> tuple[pd.DataFrame, str]:
    """Fetch non-degenerate f01 PRATE valid at the target f00 analysis time."""

    flat, units_by_variable, uri = fetch_cropped_hrrr_fields(
        valid_time_utc=valid_time_utc,
        bbox=bbox,
        bbox_padding_degrees=bbox_padding_degrees,
        availability_lag_hours=availability_lag_hours,
        variables={"precip_rate_kg_m2_s": HRRR_VARIABLES["precip_rate_kg_m2_s"]},
        forecast_hour=forecast_hour,
    )
    return (
        normalize_forecast_precip_grid(
            flat,
            units=units_by_variable["precip_rate_kg_m2_s"],
            valid_time_utc=valid_time_utc,
            forecast_hour=forecast_hour,
        ),
        uri,
    )


def replace_source_grid_precipitation(
    source_grid: pd.DataFrame, precip_grid: pd.DataFrame
) -> pd.DataFrame:
    """Replace the unusable f00 PRATE values with matched f01 forecast PRATE."""

    hashes = set(source_grid["SOURCE_GRID_HASH"].astype(str))
    precip_hashes = set(precip_grid["SOURCE_GRID_HASH"].astype(str))
    if len(hashes) != 1 or hashes != precip_hashes:
        raise ValueError("The f00 core and f01 precipitation grids do not match.")
    if len(source_grid) != len(precip_grid):
        raise ValueError("The f00 core and f01 precipitation grids have different row counts.")
    left = source_grid.sort_values("SOURCE_GRID_INDEX").reset_index(drop=True).copy()
    right = precip_grid.sort_values("SOURCE_GRID_INDEX").reset_index(drop=True)
    if not np.array_equal(
        left["SOURCE_GRID_INDEX"].to_numpy(), right["SOURCE_GRID_INDEX"].to_numpy()
    ) or not np.allclose(
        left[["SOURCE_LAT", "SOURCE_LON"]].to_numpy(dtype=float),
        right[["SOURCE_LAT", "SOURCE_LON"]].to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-7,
    ):
        raise ValueError("The f00 core and f01 precipitation grid coordinates do not match.")
    left["PRECIP_RATE_KG_M2_S"] = right["PRECIP_RATE_KG_M2_S"].to_numpy(dtype=float)
    result = left[RAW_COLUMNS]
    result.attrs.update(source_grid.attrs)
    return result


def fetch_cropped_hrrr_grid(
    *,
    valid_time_utc: pd.Timestamp,
    bbox: Mapping[str, float],
    bbox_padding_degrees: float,
    availability_lag_hours: int,
    include_precipitation: bool = True,
) -> tuple[pd.DataFrame, str]:
    """Fetch one HRRR analysis and return a strict cropped source-grid frame."""

    flat, units_by_variable, uri = fetch_cropped_hrrr_fields(
        valid_time_utc=valid_time_utc,
        bbox=bbox,
        bbox_padding_degrees=bbox_padding_degrees,
        availability_lag_hours=availability_lag_hours,
        variables={name: selector for name, selector in HRRR_VARIABLES.items()
                   if include_precipitation or name != "precip_rate_kg_m2_s"},
    )
    return (
        normalize_flat_grid(
            flat,
            units_by_variable=units_by_variable,
            valid_time_utc=valid_time_utc,
            availability_lag_hours=availability_lag_hours,
            include_precipitation=include_precipitation,
        ),
        uri,
    )
