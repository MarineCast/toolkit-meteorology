"""Strict HRRR variable identification and source-grid normalization helpers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BoundingBox:
    """Geographic crop bounds for one acquired source grid."""

    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float


_COORD_NAMES = {"latitude", "longitude", "lat", "lon", "time", "step", "valid_time"}
_EXPECTED_VARIABLE_METADATA = {
    "temperature_2m_k": {
        "short_names": {"2t", "t", "tmp"},
        "name_phrases": {"temperature", "2 metre temperature"},
        "var_names": {"t2m", "t", "tmp"},
        "type_of_level": {"heightaboveground"},
        "levels": {2.0},
    },
    "relative_humidity_2m_pct": {
        "short_names": {"2r", "r2", "rh", "r"},
        "name_phrases": {"relative humidity", "2 metre relative humidity"},
        "var_names": {"r2", "r", "rh"},
        "type_of_level": {"heightaboveground"},
        "levels": {2.0},
    },
    "u_wind_10m_ms": {
        "short_names": {"10u", "u10", "u", "ugrd"},
        "name_phrases": {"10 metre u wind", "u wind component", "u-component"},
        "var_names": {"u10", "u", "ugrd"},
        "type_of_level": {"heightaboveground"},
        "levels": {10.0},
    },
    "v_wind_10m_ms": {
        "short_names": {"10v", "v10", "v", "vgrd"},
        "name_phrases": {"10 metre v wind", "v wind component", "v-component"},
        "var_names": {"v10", "v", "vgrd"},
        "type_of_level": {"heightaboveground"},
        "levels": {10.0},
    },
    "wind_gust_surface_ms": {
        "short_names": {"gust"},
        "name_phrases": {"wind speed (gust)", "gust"},
        "var_names": {"gust"},
        "type_of_level": {"surface"},
    },
    "visibility_m": {
        "short_names": {"vis"},
        "name_phrases": {"visibility"},
        "var_names": {"vis", "visibility"},
        "type_of_level": {"surface"},
    },
    "total_cloud_cover_pct": {
        "short_names": {"tcc", "tcdc"},
        "name_phrases": {"total cloud cover"},
        "var_names": {"tcc", "tcdc"},
        "type_of_level": {"atmosphere", "entireatmosphere"},
    },
    "precip_rate_kg_m2_s": {
        "short_names": {"prate"},
        "name_phrases": {"precipitation rate"},
        "var_names": {"prate"},
        "type_of_level": {"surface"},
    },
    "mean_sea_level_pressure_pa": {
        "short_names": {"mslma", "prmsl"},
        "name_phrases": {"mean sea level pressure", "mslp"},
        "var_names": {"mslma", "prmsl", "mslp"},
        "type_of_level": {"meansea", "meansealevel"},
    },
}


def iter_dataset_parts(ds_or_parts: Any):
    """
    Yield individual xarray.Dataset objects from either a single Dataset
    or a list/tuple of Datasets returned by Herbie/cfgrib.
    """
    if isinstance(ds_or_parts, xr.Dataset):
        yield ds_or_parts
        return
    if isinstance(ds_or_parts, (list, tuple)):
        for idx, part in enumerate(ds_or_parts):
            if isinstance(part, xr.Dataset):
                yield part
            else:
                raise TypeError(
                    f"Expected xarray.Dataset at dataset part {idx}, got {type(part).__name__}."
                )
        return
    raise TypeError(
        f"Expected xarray.Dataset or list/tuple of Datasets, got {type(ds_or_parts).__name__}."
    )


def _dataset_parts(ds: Any) -> list[xr.Dataset]:
    return list(iter_dataset_parts(ds))


def describe_hrrr_dataset_parts(ds_or_parts: Any) -> list[dict]:
    """
    Return structured metadata describing all available variables
    in all returned HRRR dataset parts.
    """
    records: list[dict] = []
    for part_idx, ds in enumerate(iter_dataset_parts(ds_or_parts)):
        for var_name, da in ds.data_vars.items():
            attrs = getattr(da, "attrs", {}) or {}
            records.append(
                {
                    "dataset_part": part_idx,
                    "var_name": str(var_name),
                    "dims": tuple(da.dims),
                    "shape": tuple(da.shape),
                    "dtype": str(da.dtype),
                    "GRIB_shortName": attrs.get("GRIB_shortName"),
                    "GRIB_name": attrs.get("GRIB_name"),
                    "GRIB_typeOfLevel": attrs.get("GRIB_typeOfLevel"),
                    "GRIB_level": attrs.get("GRIB_level"),
                    "GRIB_stepType": attrs.get("GRIB_stepType"),
                    "GRIB_units": attrs.get("GRIB_units"),
                    "long_name": attrs.get("long_name"),
                    "units": attrs.get("units"),
                    "standard_name": attrs.get("standard_name"),
                }
            )
    return records


def _format_inventory(records: list[dict]) -> str:
    if not records:
        return "<no data variables>"
    return "\n".join(
        f"part={v['dataset_part']} var={v['var_name']} "
        f"GRIB_shortName={v.get('GRIB_shortName')} "
        f"GRIB_name={v.get('GRIB_name')} "
        f"GRIB_typeOfLevel={v.get('GRIB_typeOfLevel')} "
        f"GRIB_level={v.get('GRIB_level')} "
        f"dims={v.get('dims')} shape={v.get('shape')}"
        for v in records
    )


def list_hrrr_variable_names(ds_or_parts: Any) -> list[str]:
    """Return compact variable labels for all HRRR dataset parts."""
    return [
        (
            f"part={record['dataset_part']} var={record['var_name']} "
            f"shortName={record.get('GRIB_shortName')} "
            f"name={record.get('GRIB_name')} "
            f"typeOfLevel={record.get('GRIB_typeOfLevel')}"
        )
        for record in describe_hrrr_dataset_parts(ds_or_parts)
    ]


def _search_code(search: str) -> str:
    match = re.search(r":?([A-Z][A-Z0-9]+):", str(search))
    return match.group(1) if match else str(search).strip(":").split(":")[0].upper()


def _numeric_data_vars(ds: Any) -> list[str]:
    out = []
    for name, da in ds.data_vars.items():
        if name in _COORD_NAMES:
            continue
        try:
            if np.issubdtype(da.dtype, np.number) and int(da.size) > 0:
                out.append(str(name))
        except Exception:
            continue
    return out


def _candidate_score(output_name: str, ds: xr.Dataset, var_name: str, search_code: str) -> int:
    da = ds[var_name]
    attrs = getattr(da, "attrs", {}) or {}
    expected = _EXPECTED_VARIABLE_METADATA.get(output_name, {})
    short_name = str(attrs.get("GRIB_shortName", "")).lower()
    grib_name = str(attrs.get("GRIB_name", "")).lower()
    level_type = str(attrs.get("GRIB_typeOfLevel", "")).lower()
    long_name = str(attrs.get("long_name", "")).lower()
    standard_name = str(attrs.get("standard_name", "")).lower()
    units = str(attrs.get("units", attrs.get("GRIB_units", ""))).lower()
    var_lower = str(var_name).lower()
    search_lower = str(search_code).lower()

    score = 0
    if short_name in expected.get("short_names", set()):
        score += 200
    if var_lower in expected.get("var_names", set()):
        score += 150
    for phrase in expected.get("name_phrases", set()):
        phrase_lower = str(phrase).lower()
        if phrase_lower in grib_name:
            score += 120
        if phrase_lower in long_name:
            score += 80
        if phrase_lower in standard_name:
            score += 60
    if level_type and level_type in expected.get("type_of_level", set()):
        score += 50
    if (
        search_lower
        and search_lower in expected.get("short_names", set())
        and short_name == search_lower
    ):
        score += 40
    if output_name == "relative_humidity_2m_pct" and ("%" in units or units == "percent"):
        score += 20
    return score


def _log_available_variable_names(records: list[dict]) -> None:
    labels = [
        (
            f"part={record['dataset_part']}:{record['var_name']}"
            f"[{record.get('GRIB_shortName')}; {record.get('GRIB_name')}; "
            f"{record.get('GRIB_typeOfLevel')}]"
        )
        for record in records
    ]
    LOGGER.debug("Available HRRR variables: %s", "; ".join(labels) if labels else "<none>")


def _validate_selected_metadata(output_name: str, da: Any) -> None:
    expected = _EXPECTED_VARIABLE_METADATA[output_name]
    attrs = getattr(da, "attrs", {}) or {}
    short_name = str(attrs.get("GRIB_shortName", "")).lower()
    level_type = str(attrs.get("GRIB_typeOfLevel", "")).lower()
    level_raw = attrs.get("GRIB_level")
    if short_name not in expected["short_names"]:
        raise ValueError(
            f"Strict HRRR selector mismatch for {output_name}: GRIB_shortName={short_name!r}."
        )
    expected_types = expected.get("type_of_level")
    if expected_types and level_type not in expected_types:
        raise ValueError(
            f"Strict HRRR selector mismatch for {output_name}: " f"GRIB_typeOfLevel={level_type!r}."
        )
    expected_levels = expected.get("levels")
    if expected_levels:
        coordinate_name = next(
            (name for name in da.coords if str(name).lower() == level_type), None
        )
        if (
            level_raw is None
            and coordinate_name is not None
            and da.coords[coordinate_name].ndim == 0
        ):
            level_raw = da.coords[coordinate_name].values
        try:
            level = float(level_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Strict HRRR selector lacks a numeric GRIB level for {output_name}."
            ) from exc
        if level not in expected_levels:
            raise ValueError(
                f"Strict HRRR selector mismatch for {output_name}: GRIB_level={level}."
            )


def _raise_inference_error(ds: Any, output_name: str, search: str) -> None:
    available = describe_hrrr_dataset_parts(ds)
    raise ValueError(
        f"Could not infer HRRR variable for output_name={output_name!r} "
        f"search={search!r}.\nAvailable HRRR variables:\n{_format_inventory(available)}"
    )


def infer_required_hrrr_variables(
    ds: Any,
    variable_searches: dict[str, str],
    allow_missing: bool = False,
    reject_ambiguous: bool = False,
) -> dict[str, tuple[Any, str] | None]:
    mapping: dict[str, tuple[Any, str] | None] = {}
    used: set[tuple[int, str]] = set()
    parts = _dataset_parts(ds)
    inventory = describe_hrrr_dataset_parts(ds)
    _log_available_variable_names(inventory)
    for output_name, search in variable_searches.items():
        code = _search_code(search)
        scored: list[tuple[int, int, str]] = []
        for part_idx, part in enumerate(parts):
            for var_name in _numeric_data_vars(part):
                if (part_idx, var_name) in used:
                    continue
                score = _candidate_score(output_name, part, var_name, code)
                scored.append((score, part_idx, var_name))
        scored.sort(key=lambda item: (item[0], -item[1], item[2]), reverse=True)
        best_score: int | None = None
        if not scored or scored[0][0] <= 0:
            if allow_missing:
                mapping[output_name] = None
                LOGGER.debug(
                    "Missing HRRR variable output_name=%s search=%s; filling with nulls",
                    output_name,
                    search,
                )
                continue
            else:
                _raise_inference_error(ds, output_name, search)
        else:
            best_score, part_idx, var_name = scored[0]
            if (
                reject_ambiguous
                and len(scored) > 1
                and scored[1][0] == best_score
                and best_score > 0
            ):
                raise ValueError(
                    "Ambiguous HRRR variable mapping for "
                    f"output_name={output_name!r}: {scored[0][2]!r} and "
                    f"{scored[1][2]!r} both scored {best_score}."
                )
        used.add((part_idx, var_name))
        mapping[output_name] = (parts[part_idx], var_name)
        da = parts[part_idx][var_name]
        _validate_selected_metadata(output_name, da)
        attrs = getattr(da, "attrs", {}) or {}
        LOGGER.debug(
            "Matched HRRR variable output_name=%s search=%s var=%s part=%s score=%s GRIB_shortName=%s GRIB_name=%s GRIB_typeOfLevel=%s level=%s",
            output_name,
            search,
            var_name,
            part_idx,
            best_score,
            attrs.get("GRIB_shortName"),
            attrs.get("GRIB_name"),
            attrs.get("GRIB_typeOfLevel"),
            attrs.get("GRIB_level"),
        )
    return mapping


def _coord_array(ds: Any, names: tuple[str, ...]) -> np.ndarray:
    for name in names:
        if name in ds.coords:
            return np.asarray(ds.coords[name].values)
        if name in ds:
            return np.asarray(ds[name].values)
    raise ValueError(f"Dataset is missing coordinate candidates: {names}")


def _standardize_coords(ds: Any) -> tuple[np.ndarray, np.ndarray]:
    lat = _coord_array(ds, ("latitude", "lat"))
    lon = _coord_array(ds, ("longitude", "lon"))
    lon = np.where(lon > 180, lon - 360, lon)
    return np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)


def _as_2d_values(da: Any) -> np.ndarray:
    values = np.asarray(da.squeeze().values)
    if values.ndim < 2:
        raise ValueError(f"HRRR variable {da.name!r} is not at least 2-dimensional after squeeze.")
    if values.ndim > 2:
        values = values.reshape((-1,) + values.shape[-2:])[-1]
    return values.reshape(values.shape[-2:])


def _lat_lon_for_values(ds: Any, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    lat, lon = _standardize_coords(ds)
    if lat.ndim > 2:
        lat = np.squeeze(lat)[-shape[0] :, -shape[1] :]
    if lon.ndim > 2:
        lon = np.squeeze(lon)[-shape[0] :, -shape[1] :]
    if lat.ndim == 1 and lon.ndim == 1:
        lon, lat = np.meshgrid(lon, lat)
    return np.asarray(lat).reshape(shape), np.asarray(lon).reshape(shape)


def dataset_to_flat_variable_grid(
    ds: Any,
    variable_mapping: dict[str, tuple[Any, str] | None],
    bbox: BoundingBox,
    pad_deg: float = 0.2,
) -> pd.DataFrame:
    if not variable_mapping:
        raise ValueError("variable_mapping must be non-empty.")
    present_mapping = {name: value for name, value in variable_mapping.items() if value is not None}
    if not present_mapping:
        raise ValueError("At least one HRRR variable must be present to build the flat grid.")
    first_output = next(iter(present_mapping))
    first_ds, first_var = present_mapping[first_output]
    first_values = _as_2d_values(first_ds[first_var])
    lat, lon = _lat_lon_for_values(first_ds, first_values.shape)
    mask = (
        (lat >= bbox.min_lat - pad_deg)
        & (lat <= bbox.max_lat + pad_deg)
        & (lon >= bbox.min_lon - pad_deg)
        & (lon <= bbox.max_lon + pad_deg)
    )
    out = pd.DataFrame(
        {
            "hrrr_lat": lat[mask].astype(float),
            "hrrr_lon": lon[mask].astype(float),
        }
    )
    for output_name, mapping_value in variable_mapping.items():
        if mapping_value is None:
            out[output_name] = np.nan
            continue
        var_ds, var_name = mapping_value
        if var_name not in var_ds:
            raise ValueError(f"Required HRRR data variable missing: {var_name}")
        values = _as_2d_values(var_ds[var_name])
        if values.shape != first_values.shape:
            raise ValueError(f"HRRR variable {var_name!r} shape does not match coordinate grid.")
        out[output_name] = values[mask]
    return out.dropna(subset=["hrrr_lat", "hrrr_lon"]).reset_index(drop=True)
