"""One-message-at-a-time HRRR native crop decoding for the bounded UTC pilot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod

from ..era5.resources import Budget, LimitExceeded
from ..surface_weather.source import normalize_hrrr_values
from ..surface_weather.source_validation import BoundingBox, validate_grid_coverage

# discipline, parameterCategory, parameterNumber, allowed first fixed surfaces, level
FIELD_HEADERS = {
    "temperature_2m_k": (0, 0, 0, {103}, 2),
    "relative_humidity_2m_pct": (0, 1, 1, {103}, 2),
    "u_wind_10m_ms": (0, 2, 2, {103}, 10),
    "v_wind_10m_ms": (0, 2, 3, {103}, 10),
    "wind_gust_surface_ms": (0, 2, 22, {1}, 0),
    "visibility_m": (0, 19, 0, {1}, 0),
    "total_cloud_cover_pct": (0, 6, 1, {10, 200}, 0),
    "mean_sea_level_pressure_pa": (0, 3, 198, {101}, 0),
}
METRIC_INPUTS = {
    "TEMPERATURE_2M_C": ("temperature_2m_k", lambda x: x - 273.15),
    "RELATIVE_HUMIDITY_2M_PCT": ("relative_humidity_2m_pct", lambda x: x),
    "U_WIND_10M_MS": ("u_wind_10m_ms", lambda x: x),
    "V_WIND_10M_MS": ("v_wind_10m_ms", lambda x: x),
    "WIND_GUST_SURFACE_MS": ("wind_gust_surface_ms", lambda x: x),
    "VISIBILITY_KM": ("visibility_m", lambda x: x / 1000),
    "TOTAL_CLOUD_COVER_PCT": ("total_cloud_cover_pct", lambda x: x),
    "MEAN_SEA_LEVEL_PRESSURE_HPA": ("mean_sea_level_pressure_pa", lambda x: x / 100),
}
GRID_KEYS = (
    "gridType",
    "Nx",
    "Ny",
    "latitudeOfFirstGridPointInDegrees",
    "longitudeOfFirstGridPointInDegrees",
    "LoVInDegrees",
    "Latin1InDegrees",
    "Latin2InDegrees",
    "DxInMetres",
    "DyInMetres",
    "iScansNegatively",
    "jScansPositively",
    "jPointsAreConsecutive",
    "alternativeRowScanning",
    "shapeOfTheEarth",
    "LaDInDegrees",
    "projectionCentreFlag",
    "scaleFactorOfRadiusOfSphericalEarth",
    "scaledValueOfRadiusOfSphericalEarth",
    "scaleFactorOfEarthMajorAxis",
    "scaledValueOfEarthMajorAxis",
    "scaleFactorOfEarthMinorAxis",
    "scaledValueOfEarthMinorAxis",
)
GEOD = Geod(ellps="WGS84")


def _header(ec, m, name, valid, expected_shape):
    def g(k):
        return ec.codes_get(m, k)

    d, c, p, surfaces, level = FIELD_HEADERS[name]
    surface = ec.codes_get_long(m, "typeOfFirstFixedSurface")
    if (
        g("edition") != 2
        or g("centre") not in ("kwbc", 7)
        or (g("discipline"), g("parameterCategory"), g("parameterNumber")) != (d, c, p)
        or surface not in surfaces
        or (surface == 103 and g("level") != level)
        or ec.codes_get_long(m, "typeOfSecondFixedSurface") != 255
        or g("stepType") != "instant"
        or g("startStep") != 0
        or g("endStep") != 0
    ):
        raise ValueError(
            "HRRR field/level/centre/f00 identity differs from the exact source request."
        )
    if (g("dataDate"), g("dataTime"), g("validityDate"), g("validityTime")) != (
        int(valid.strftime("%Y%m%d")),
        int(valid.strftime("%H%M")),
        int(valid.strftime("%Y%m%d")),
        int(valid.strftime("%H%M")),
    ):
        raise ValueError("HRRR initial/valid time is not the requested UTC f00 cycle.")
    header = {k: g(k) for k in GRID_KEYS}
    if (
        header["gridType"] != "lambert"
        or (header["Nx"], header["Ny"]) != expected_shape
        or header["DxInMetres"] != 3000
        or header["DyInMetres"] != 3000
        or header["jPointsAreConsecutive"] != 0
        or header["alternativeRowScanning"] != 0
        or header["iScansNegatively"] != 0
        or header["jScansPositively"] != 1
    ):
        raise ValueError(
            "HRRR Lambert 3 km native grid differs from the qualified pilot header contract."
        )
    n = g("numberOfDataPoints")
    if n != header["Nx"] * header["Ny"] or n > 2_000_000:
        raise LimitExceeded("Native point count exceeds the bounded HRRR CONUS decoder allowance.")
    return header, n, str(g("units"))


def _rotation(lat, lon, indices, nx, ny):
    # Compute bearings only at cropped points, without full-size rotation arrays.
    x = indices % nx
    y = indices // nx
    a = np.where(x < nx - 1, indices, indices - 1)
    b = a + 1
    i, _, distance_i = GEOD.inv(lon[a], lat[a], lon[b], lat[b])
    a = np.where(y < ny - 1, indices, indices - nx)
    b = a + nx
    j, _, distance_j = GEOD.inv(lon[a], lat[a], lon[b], lat[b])
    ir, jr = np.deg2rad(i), np.deg2rad(j)
    if (distance_i <= 0).any() or (distance_j <= 0).any() or (abs(np.cos(ir - jr)) > 0.05).any():
        raise ValueError("Decoded source wind axes are degenerate or nonorthogonal.")
    return i, np.sign(np.sin(jr - ir))


def validate_hour(frame, evidence, valid):
    expected = {metric for metric in METRIC_INPUTS} | {"WIND_SPEED_10M_MS"}
    if (
        frame.empty
        or len(frame) > 4096
        or frame.SOURCE_GRID_INDEX.duplicated().any()
        or set(frame.VALID_TIME_UTC) != {valid.isoformat()}
        or set(frame.INIT_TIME_UTC) != {valid.isoformat()}
        or set(frame.SOURCE_MODEL) != {"HRRR"}
        or set(frame.FORECAST_HOUR) != {0}
        or frame.SOURCE_GRID_HASH.nunique() != 1
        or frame.SOURCE_GRID_HASH.iloc[0] != evidence["source_grid_hash"]
    ):
        raise ValueError("Native hourly identity/key/cardinality contract failed.")
    if (
        not pd.api.types.is_integer_dtype(frame.SOURCE_GRID_INDEX)
        or (frame.SOURCE_GRID_INDEX < 0).any()
        or (frame.SOURCE_GRID_INDEX >= evidence["native_points"]).any()
    ):
        raise ValueError("Native indices must be original nonnegative full-grid integer positions.")
    coords = frame[["SOURCE_LAT", "SOURCE_LON"]].to_numpy(float)
    if (
        not np.isfinite(coords).all()
        or (abs(coords[:, 0]) > 90).any()
        or (abs(coords[:, 1]) > 180).any()
    ):
        raise ValueError("Native coordinates must be finite WGS84.")
    for metric in expected:
        values = frame[metric].to_numpy(float)
        if np.isinf(values).any():
            raise ValueError("Infinite native metric cannot become a value.")
        finite = values[np.isfinite(values)]
        bounds = {
            "TEMPERATURE_2M_C": (-123.15, 76.85),
            "RELATIVE_HUMIDITY_2M_PCT": (0, 100),
            "TOTAL_CLOUD_COVER_PCT": (0, 100),
            "MEAN_SEA_LEVEL_PRESSURE_HPA": (700, 1200),
            "VISIBILITY_KM": (0, 1000),
            "WIND_GUST_SURFACE_MS": (0, 200),
            "WIND_SPEED_10M_MS": (0, 200),
            "U_WIND_10M_MS": (-200, 200),
            "V_WIND_10M_MS": (-200, 200),
        }[metric]
        if ((finite < bounds[0]) | (finite > bounds[1])).any():
            raise ValueError("Native metric violates physical acceptance limits.")
    if not np.allclose(
        frame.WIND_SPEED_10M_MS, np.hypot(frame.U_WIND_10M_MS, frame.V_WIND_10M_MS), equal_nan=True
    ):
        raise ValueError("Hourly wind speed must be the Earth-relative component magnitude.")
    if not np.allclose(
        frame.WIND_SPEED_10M_MS,
        np.hypot(frame.RAW_U_WIND_10M_MS, frame.RAW_V_WIND_10M_MS),
        equal_nan=True,
    ):
        raise ValueError("Wind rotation changed the source vector magnitude.")


def decode_hour(
    files: dict[str, Path],
    *,
    valid: pd.Timestamp,
    bbox: list,
    halo: float,
    budget: Budget,
    expected_shape=(1799, 1059),
):
    import eccodes as ec

    if set(files) != set(FIELD_HEADERS):
        raise ValueError("Exactly the eight separate f00 fields required.")
    values = {}
    raw_source = {}
    units = {}
    grid = None
    lat = lon = indices = None
    coverage = None
    wind_flags = []
    for name, path in files.items():
        budget.checkpoint(additional_memory=2 * Path(path).stat().st_size)
        with Path(path).open("rb") as source:
            m = ec.codes_grib_new_from_file(source)
            if m is None:
                raise ValueError("Selected GRIB field is empty.")
            try:
                header, n, unit = _header(ec, m, name, valid, expected_shape)
                budget.checkpoint(additional_memory=n * 256 + 2 * Path(path).stat().st_size)
                if grid is None:
                    grid = header
                    lat = np.asarray(ec.codes_get_array(m, "latitudes"), dtype=float)
                    lon = np.asarray(ec.codes_get_array(m, "longitudes"), dtype=float)
                    lon = np.where(lon > 180, lon - 360, lon)
                    west, south, east, north = bbox
                    mask = (
                        (lat >= south - halo)
                        & (lat <= north + halo)
                        & (lon >= west - halo)
                        & (lon <= east + halo)
                    )
                    indices = np.flatnonzero(mask)
                    if not 0 < len(indices) <= 4096:
                        raise LimitExceeded(
                            "Native crop size exceeds the reviewed 4096-point allowance."
                        )
                    crop = pd.DataFrame({"hrrr_lat": lat[indices], "hrrr_lon": lon[indices]})
                    coverage = validate_grid_coverage(
                        lat.reshape(header["Ny"], header["Nx"]),
                        lon.reshape(header["Ny"], header["Nx"]),
                        crop,
                        BoundingBox(min_lat=south, max_lat=north, min_lon=west, max_lon=east),
                    )
                elif header != grid:
                    raise ValueError("Native source grid changed across fields.")
                raw = np.asarray(ec.codes_get_values(m), dtype=float)
                if len(raw) != n:
                    raise ValueError("GRIB values do not match native grid size.")
                if ec.codes_get(m, "bitmapPresent"):
                    bitmap = np.asarray(ec.codes_get_array(m, "bitmap"))
                    raw[bitmap == 0] = np.nan
                raw_source[name] = raw[indices].copy()
                selected = normalize_hrrr_values(
                    pd.Series(raw_source[name]), variable=name, units=unit
                ).to_numpy(float)
                values[name] = selected
                units[name] = unit
                if name in ("u_wind_10m_ms", "v_wind_10m_ms"):
                    flag = ec.codes_get(m, "uvRelativeToGrid")
                    if flag not in (0, 1):
                        raise ValueError("Wind reference flag is absent or invalid.")
                    wind_flags.append(flag)
            finally:
                ec.codes_release(m)
            extra = ec.codes_grib_new_from_file(source)
            if extra is not None:
                ec.codes_release(extra)
                raise ValueError("A selected range contains multiple GRIB messages.")
    if len(wind_flags) != 2 or wind_flags[0] != wind_flags[1]:
        raise ValueError("U/V source wind-reference flags disagree.")
    raw_u = values["u_wind_10m_ms"].copy()
    raw_v = values["v_wind_10m_ms"].copy()
    bearing, hand = _rotation(lat, lon, indices, grid["Nx"], grid["Ny"])
    if wind_flags[0]:
        rad = np.deg2rad(bearing)
        values["u_wind_10m_ms"] = raw_u * np.sin(rad) + hand * raw_v * np.cos(rad)
        values["v_wind_10m_ms"] = raw_u * np.cos(rad) - hand * raw_v * np.sin(rad)
    digest = hashlib.sha256()
    digest.update(json.dumps(grid, sort_keys=True, separators=(",", ":")).encode())
    digest.update(np.asarray(lat, dtype="<f8").tobytes())
    digest.update(np.asarray(lon, dtype="<f8").tobytes())
    source_hash = digest.hexdigest()
    frame = pd.DataFrame(
        dict(
            SOURCE_GRID_INDEX=indices.astype("int32"),
            SOURCE_LAT=lat[indices],
            SOURCE_LON=lon[indices],
            VALID_TIME_UTC=valid.isoformat(),
            INIT_TIME_UTC=valid.isoformat(),
            SOURCE_MODEL="HRRR",
            FORECAST_HOUR=0,
            SOURCE_GRID_HASH=source_hash,
            RAW_U_WIND_10M_MS=raw_u,
            RAW_V_WIND_10M_MS=raw_v,
            SOURCE_I_BEARING_DEG=bearing,
            SOURCE_J_HANDEDNESS=hand,
        )
    )
    for name, raw in raw_source.items():
        frame["RAW_SOURCE_" + name] = raw
    for metric, (name, convert) in METRIC_INPUTS.items():
        frame[metric] = convert(values[name])
    frame["WIND_SPEED_10M_MS"] = np.hypot(frame.U_WIND_10M_MS, frame.V_WIND_10M_MS)
    evidence = dict(
        source_grid_hash=source_hash,
        header=grid,
        native_points=n,
        crop_points=len(frame),
        units=units,
        native_footprint_wkb_hex=coverage.native_footprint.wkb_hex,
        max_nearest_distance_m=coverage.max_nearest_distance_m,
        source_wind_basis="grid_relative" if wind_flags[0] else "earth_relative",
        source_evidence_kind="retained_decoded_hrrr",
        historical_publication_time="unknown",
    )
    validate_hour(frame, evidence, valid)
    budget.checkpoint()
    return frame, evidence
