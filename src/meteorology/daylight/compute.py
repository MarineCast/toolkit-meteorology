"""Compute deterministic daylight panels from existing H3 centroid tables.

Schema notes
------------
Date-panel outputs are keyed by ``h3`` and ``date``. They include calendar
fields plus ``solar_day_365``, a stable non-leap solar-day index used for the
daylight formula. Feb 29 is marked with ``is_leap_day`` and mapped to solar day
60, the same solar-day proxy as Mar 1.

Compact day-of-year outputs are keyed by ``h3`` and ``day_of_year`` and include
``month``, ``day``, ``month_day``, ``is_leap_day``, and ``solar_day_365`` so
callers do not need to infer leap-year semantics from ordinal day alone.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

OUTPUT_COLUMNS = [
    "h3",
    "date",
    "year",
    "day_of_year",
    "month",
    "day",
    "month_day",
    "is_leap_day",
    "solar_day_365",
    "centroid_lat",
    "centroid_lon",
    "daylight_hours",
    "daylight_fraction",
]
DAY_OF_YEAR_OUTPUT_COLUMNS = [
    "h3",
    "day_of_year",
    "month",
    "day",
    "month_day",
    "is_leap_day",
    "solar_day_365",
    "centroid_lat",
    "centroid_lon",
    "daylight_hours",
    "daylight_fraction",
]

H3_ALIASES = ("h3", "H3_INDEX", "h3_index", "h3_cell", "cell", "hex_id")
LATITUDE_ALIASES = ("centroid_lat", "LAT", "lat", "latitude", "centroid_y")
LONGITUDE_ALIASES = ("centroid_lon", "LON", "lon", "longitude", "centroid_x")


def solar_day_365(month: int, day: int) -> int:
    """Return a stable non-leap solar-day index for a month/day pair.

    Feb 29 is explicit and maps to 60, the same solar-day proxy as Mar 1.
    """
    month_int = int(month)
    day_int = int(day)
    if month_int == 2 and day_int == 29:
        return 60
    ts = pd.Timestamp(year=2001, month=month_int, day=day_int)
    return int(ts.dayofyear)


def _solar_day_365_from_dates(dates: pd.Series) -> pd.Series:
    month = dates.dt.month.astype("int64")
    day = dates.dt.day.astype("int64")
    ordinal = dates.dt.dayofyear.astype("int64")
    after_feb_29 = dates.dt.is_leap_year & ((month > 2) | ((month == 2) & (day > 29)))
    solar = ordinal.where(~after_feb_29, ordinal - 1)
    return solar.where(~((month == 2) & (day == 29)), 60).astype("int64")


def _daylight_hours_array(latitude_deg: np.ndarray, solar_day: np.ndarray) -> np.ndarray:
    lat = np.deg2rad(np.clip(latitude_deg.astype(float), -90.0, 90.0))
    day = solar_day.astype(float)
    declination = np.deg2rad(23.44 * np.sin((2.0 * np.pi / 365.0) * (day - 81.0)))
    hour_angle_arg = -np.tan(lat) * np.tan(declination)
    daylight = np.empty(len(day), dtype=float)
    daylight[hour_angle_arg >= 1.0] = 0.0
    daylight[hour_angle_arg <= -1.0] = 24.0
    mask = (hour_angle_arg > -1.0) & (hour_angle_arg < 1.0)
    daylight[mask] = (24.0 / np.pi) * np.arccos(hour_angle_arg[mask])
    return np.clip(daylight, 0.0, 24.0)


def compute_daylight_hours(latitude_deg: float, day_of_year: int) -> float:
    """Approximate daylight hours for a latitude and stable solar day.

    The calculation uses a standard dependency-light solar declination
    approximation and ignores atmospheric refraction and local horizon effects.
    The ``day_of_year`` argument is interpreted as a stable 1..365 solar-day
    index. A value of 366 is accepted for backward compatibility and mapped to
    365.
    """
    if pd.isna(latitude_deg):
        raise ValueError("latitude_deg must be non-null.")
    if not 1 <= int(day_of_year) <= 366:
        raise ValueError("day_of_year must be in [1, 366].")

    solar_day = min(int(day_of_year), 365)
    lat_rad = math.radians(float(np.clip(latitude_deg, -90.0, 90.0)))
    declination_deg = 23.44 * math.sin((2.0 * math.pi / 365.0) * (solar_day - 81))
    declination_rad = math.radians(declination_deg)
    hour_angle_arg = -math.tan(lat_rad) * math.tan(declination_rad)

    if hour_angle_arg >= 1.0:
        return 0.0
    if hour_angle_arg <= -1.0:
        return 24.0
    return float((24.0 / math.pi) * math.acos(hour_angle_arg))


def _coerce_date(value: str | date | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise ValueError(f"Invalid date value: {value!r}")
    return ts.normalize()


def _resolve_column(df: pd.DataFrame, requested: str, aliases: tuple[str, ...]) -> str | None:
    if requested in df.columns:
        return requested
    for alias in aliases:
        if alias in df.columns:
            return alias
    return None


def prepare_h3_centroids(
    h3_df: pd.DataFrame,
    h3_column: str = "h3",
    latitude_column: str = "centroid_lat",
    longitude_column: str = "centroid_lon",
) -> pd.DataFrame:
    """Return unique H3 centroid columns normalized to h3/centroid_lat/centroid_lon."""
    resolved_h3 = _resolve_column(h3_df, h3_column, H3_ALIASES)
    resolved_lat = _resolve_column(h3_df, latitude_column, LATITUDE_ALIASES)
    resolved_lon = _resolve_column(h3_df, longitude_column, LONGITUDE_ALIASES)
    missing = [
        requested
        for requested, resolved in (
            (h3_column, resolved_h3),
            (latitude_column, resolved_lat),
            (longitude_column, resolved_lon),
        )
        if resolved is None
    ]
    if missing:
        available = ", ".join(map(str, h3_df.columns))
        raise ValueError(
            "H3 centroid table is missing required columns or known aliases: "
            f"{missing}. Available columns: {available}"
        )

    cells = (
        h3_df[[resolved_h3, resolved_lat, resolved_lon]]
        .rename(
            columns={
                resolved_h3: "h3",
                resolved_lat: "centroid_lat",
                resolved_lon: "centroid_lon",
            }
        )
        .copy()
    )
    cells["h3"] = cells["h3"].astype("string")
    cells["centroid_lat"] = pd.to_numeric(cells["centroid_lat"], errors="coerce")
    cells["centroid_lon"] = pd.to_numeric(cells["centroid_lon"], errors="coerce")
    cells = cells.drop_duplicates(subset=["h3"], keep="first").reset_index(drop=True)
    if cells[["h3", "centroid_lat", "centroid_lon"]].isna().any().any():
        raise ValueError("H3 centroid table contains null h3, latitude, or longitude values.")
    if not cells["centroid_lat"].between(-90.0, 90.0).all():
        raise ValueError("Input centroid latitude contains values outside [-90, 90].")
    if not cells["centroid_lon"].between(-180.0, 180.0).all():
        raise ValueError("Input centroid longitude contains values outside [-180, 180].")
    return cells


def compute_daylight_table(
    h3_df: pd.DataFrame,
    start_date: str | date | pd.Timestamp,
    end_date: str | date | pd.Timestamp,
    h3_column: str = "h3",
    latitude_column: str = "centroid_lat",
    longitude_column: str = "centroid_lon",
) -> pd.DataFrame:
    """Build a model-ready H3 x date panel of deterministic daylight features."""
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date.")

    cells = prepare_h3_centroids(
        h3_df,
        h3_column=h3_column,
        latitude_column=latitude_column,
        longitude_column=longitude_column,
    )

    dates = pd.DataFrame({"date": pd.date_range(start, end, freq="D")})
    cells["_join_key"] = 1
    dates["_join_key"] = 1
    out = cells.merge(dates, on="_join_key", how="inner").drop(columns="_join_key")
    out["year"] = out["date"].dt.year.astype("int64")
    out["day_of_year"] = out["date"].dt.dayofyear.astype("int64")
    out["month"] = out["date"].dt.month.astype("int64")
    out["day"] = out["date"].dt.day.astype("int64")
    out["month_day"] = out["date"].dt.strftime("%m-%d")
    out["is_leap_day"] = out["month_day"].eq("02-29")
    out["solar_day_365"] = _solar_day_365_from_dates(out["date"])

    out["daylight_hours"] = _daylight_hours_array(
        out["centroid_lat"].to_numpy(dtype=float),
        out["solar_day_365"].to_numpy(dtype=float),
    )
    out["daylight_fraction"] = out["daylight_hours"] / 24.0
    out["date"] = out["date"].dt.date.astype("string")
    return out[OUTPUT_COLUMNS].copy()


def compute_daylight_day_of_year_table(
    h3_df: pd.DataFrame,
    h3_column: str = "h3",
    latitude_column: str = "centroid_lat",
    longitude_column: str = "centroid_lon",
    max_day_of_year: int = 366,
) -> pd.DataFrame:
    """Build a compact H3 x day-of-year daylight lookup table."""
    if max_day_of_year not in {365, 366}:
        raise ValueError("max_day_of_year must be 365 or 366.")
    cells = prepare_h3_centroids(
        h3_df,
        h3_column=h3_column,
        latitude_column=latitude_column,
        longitude_column=longitude_column,
    )
    reference_year = 2000 if int(max_day_of_year) == 366 else 2001
    calendar = pd.DataFrame(
        {
            "date": pd.date_range(
                f"{reference_year}-01-01",
                periods=int(max_day_of_year),
                freq="D",
            )
        }
    )
    calendar["day_of_year"] = calendar["date"].dt.dayofyear.astype("int64")
    calendar["month"] = calendar["date"].dt.month.astype("int64")
    calendar["day"] = calendar["date"].dt.day.astype("int64")
    calendar["month_day"] = calendar["date"].dt.strftime("%m-%d")
    calendar["is_leap_day"] = calendar["month_day"].eq("02-29")
    calendar["solar_day_365"] = _solar_day_365_from_dates(calendar["date"])
    days = calendar.drop(columns="date")
    cells["_join_key"] = 1
    days["_join_key"] = 1
    out = cells.merge(days, on="_join_key", how="inner").drop(columns="_join_key")

    out["daylight_hours"] = _daylight_hours_array(
        out["centroid_lat"].to_numpy(dtype=float),
        out["solar_day_365"].to_numpy(dtype=float),
    )
    out["daylight_fraction"] = out["daylight_hours"] / 24.0
    return out[DAY_OF_YEAR_OUTPUT_COLUMNS].copy()
