"""Dependency-light lunar astronomy and daily H3 feature computation."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..astronomy import (
    altitude_from_ra_dec,
    julian_day,
    local_day_sample_times_utc,
    solar_altitude_deg,
)

H3_ALIASES = ("h3", "H3_INDEX", "h3_index", "h3_cell", "cell", "hex_id")
LATITUDE_ALIASES = ("centroid_lat", "CENTROID_LAT", "LAT", "lat", "latitude")
LONGITUDE_ALIASES = ("centroid_lon", "CENTROID_LON", "LON", "lon", "longitude")

SYNODIC_MONTH_DAYS = 29.530588853
REFERENCE_NEW_MOON_UTC = pd.Timestamp("2000-01-06T18:14:00Z")

OUTPUT_COLUMNS = [
    "h3",
    "date",
    "year",
    "day_of_year",
    "month",
    "day",
    "month_day",
    "is_leap_day",
    "centroid_lat",
    "centroid_lon",
    "lunar_age_days",
    "lunar_phase_angle_deg",
    "lunar_illumination_fraction",
    "moon_phase_name",
    "night_hours",
    "moon_visible_hours",
    "moon_visible_dark_hours",
    "moonlit_dark_hours",
    "moon_visible_dark_fraction",
    "moonlit_dark_fraction",
    "weight_lunar_illumination",
    "weight_moonlit_dark_hours",
]


def _resolve_column(frame: pd.DataFrame, requested: str, aliases: tuple[str, ...]) -> str | None:
    if requested in frame.columns:
        return requested
    return next((alias for alias in aliases if alias in frame.columns), None)


def prepare_h3_centroids(
    h3_df: pd.DataFrame,
    h3_column: str = "h3",
    latitude_column: str = "centroid_lat",
    longitude_column: str = "centroid_lon",
) -> pd.DataFrame:
    """Normalize a unique H3 centroid table and validate coordinate bounds."""

    resolved = {
        "h3": _resolve_column(h3_df, h3_column, H3_ALIASES),
        "centroid_lat": _resolve_column(h3_df, latitude_column, LATITUDE_ALIASES),
        "centroid_lon": _resolve_column(h3_df, longitude_column, LONGITUDE_ALIASES),
    }
    missing = [name for name, column in resolved.items() if column is None]
    if missing:
        raise ValueError(
            f"H3 centroid table is missing required columns or aliases: {missing}; "
            f"available={list(h3_df.columns)}"
        )
    cells = h3_df[[resolved["h3"], resolved["centroid_lat"], resolved["centroid_lon"]]].rename(
        columns={
            resolved["h3"]: "h3",
            resolved["centroid_lat"]: "centroid_lat",
            resolved["centroid_lon"]: "centroid_lon",
        }
    )
    cells = cells.copy()
    cells["h3"] = cells["h3"].astype("string")
    cells["centroid_lat"] = pd.to_numeric(cells["centroid_lat"], errors="coerce")
    cells["centroid_lon"] = pd.to_numeric(cells["centroid_lon"], errors="coerce")
    cells = cells.drop_duplicates("h3", keep="first").reset_index(drop=True)
    if cells.isna().any().any():
        raise ValueError("H3 centroid table contains null identifiers or coordinates.")
    if not cells["centroid_lat"].between(-90.0, 90.0).all():
        raise ValueError("Input centroid latitude contains values outside [-90, 90].")
    if not cells["centroid_lon"].between(-180.0, 180.0).all():
        raise ValueError("Input centroid longitude contains values outside [-180, 180].")
    return cells


def lunar_age_days_for_datetimes(
    datetimes: pd.Series | pd.DatetimeIndex,
) -> np.ndarray:
    datetimes_utc = pd.to_datetime(datetimes, utc=True)
    elapsed_days = (datetimes_utc - REFERENCE_NEW_MOON_UTC) / pd.Timedelta(days=1)
    return np.mod(elapsed_days.to_numpy(dtype=float), SYNODIC_MONTH_DAYS)


def lunar_illumination_fraction_from_age(lunar_age_days: np.ndarray) -> np.ndarray:
    phase_angle = 2.0 * np.pi * (lunar_age_days / SYNODIC_MONTH_DAYS)
    return np.clip((1.0 - np.cos(phase_angle)) / 2.0, 0.0, 1.0)


def moon_phase_name_from_age(age_days: float) -> str:
    age = float(age_days) % SYNODIC_MONTH_DAYS
    boundaries = (
        (1.84566, "new_moon"),
        (5.53699, "waxing_crescent"),
        (9.22831, "first_quarter"),
        (12.91963, "waxing_gibbous"),
        (16.61096, "full_moon"),
        (20.30228, "waning_gibbous"),
        (23.99361, "last_quarter"),
        (27.68493, "waning_crescent"),
    )
    return next((name for boundary, name in boundaries if age < boundary), "new_moon")


def moon_altitude_deg(
    timestamp_utc: pd.Timestamp,
    lat_deg: np.ndarray,
    lon_deg: np.ndarray,
) -> np.ndarray:
    """Return the characterized low-precision lunar altitude approximation."""

    jd = float(julian_day(pd.DatetimeIndex([timestamp_utc]))[0])
    days = jd - 2451545.0
    mean_longitude = np.mod(218.316 + 13.176396 * days, 360.0)
    mean_anomaly = np.mod(134.963 + 13.064993 * days, 360.0)
    argument_latitude = np.mod(93.272 + 13.229350 * days, 360.0)
    ecliptic_longitude = np.mod(
        mean_longitude + 6.289 * math.sin(math.radians(mean_anomaly)), 360.0
    )
    ecliptic_latitude = 5.128 * math.sin(math.radians(argument_latitude))
    obliquity = 23.439 - 0.0000004 * days
    longitude_rad = math.radians(ecliptic_longitude)
    latitude_rad = math.radians(ecliptic_latitude)
    obliquity_rad = math.radians(obliquity)
    right_ascension = math.atan2(
        math.sin(longitude_rad) * math.cos(obliquity_rad)
        - math.tan(latitude_rad) * math.sin(obliquity_rad),
        math.cos(longitude_rad),
    )
    declination = math.asin(
        math.sin(latitude_rad) * math.cos(obliquity_rad)
        + math.cos(latitude_rad) * math.sin(obliquity_rad) * math.sin(longitude_rad)
    )
    return altitude_from_ra_dec(
        jd=jd,
        ra_deg=math.degrees(right_ascension) % 360.0,
        dec_deg=math.degrees(declination),
        lat_deg=np.asarray(lat_deg, dtype=float),
        lon_deg=np.asarray(lon_deg, dtype=float),
    )


def compute_lunar_date_table(
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
    sample_hour_utc: int = 12,
) -> pd.DataFrame:
    if not 0 <= int(sample_hour_utc) <= 23:
        raise ValueError("sample_hour_utc must be in [0, 23].")
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    dates = pd.date_range(start, end, freq="D")
    samples = dates.tz_localize("UTC") + pd.to_timedelta(int(sample_hour_utc), unit="h")
    age = lunar_age_days_for_datetimes(samples)
    illumination = lunar_illumination_fraction_from_age(age)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "year": dates.year.astype("int64"),
            "day_of_year": dates.dayofyear.astype("int64"),
            "month": dates.month.astype("int64"),
            "day": dates.day.astype("int64"),
            "month_day": dates.strftime("%m-%d"),
            "is_leap_day": dates.strftime("%m-%d") == "02-29",
            "lunar_age_days": age.astype("float64"),
            "lunar_phase_angle_deg": np.mod(360.0 * age / SYNODIC_MONTH_DAYS, 360.0).astype(
                "float64"
            ),
            "lunar_illumination_fraction": illumination.astype("float64"),
            "moon_phase_name": [moon_phase_name_from_age(value) for value in age],
        }
    )


def compute_moonlight_exposure_table(
    cells: pd.DataFrame,
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
    timezone_name: str = "America/Los_Angeles",
    timestep_minutes: int = 30,
    dark_sun_altitude_deg: float = -6.0,
    moon_altitude_min_deg: float = 0.0,
) -> pd.DataFrame:
    prepared = prepare_h3_centroids(cells)
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    latitudes = prepared["centroid_lat"].to_numpy(dtype=float)
    longitudes = prepared["centroid_lon"].to_numpy(dtype=float)
    timestep_hours = timestep_minutes / 60.0
    chunks = []
    for date in pd.date_range(start, end, freq="D"):
        night = np.zeros(len(prepared), dtype=float)
        visible = np.zeros(len(prepared), dtype=float)
        visible_dark = np.zeros(len(prepared), dtype=float)
        moonlit_dark = np.zeros(len(prepared), dtype=float)
        for timestamp in local_day_sample_times_utc(date, timezone_name, timestep_minutes):
            sun_altitude = solar_altitude_deg(timestamp, latitudes, longitudes)
            moon_altitude = moon_altitude_deg(timestamp, latitudes, longitudes)
            is_dark = sun_altitude < float(dark_sun_altitude_deg)
            is_visible = moon_altitude > float(moon_altitude_min_deg)
            illumination = float(
                lunar_illumination_fraction_from_age(
                    lunar_age_days_for_datetimes(pd.DatetimeIndex([timestamp]))
                )[0]
            )
            night += is_dark.astype(float) * timestep_hours
            visible += is_visible.astype(float) * timestep_hours
            overlap = is_dark & is_visible
            visible_dark += overlap.astype(float) * timestep_hours
            moonlit_dark += overlap.astype(float) * illumination * timestep_hours
        output = prepared.copy()
        output["date"] = date.strftime("%Y-%m-%d")
        output["night_hours"] = night
        output["moon_visible_hours"] = visible
        output["moon_visible_dark_hours"] = visible_dark
        output["moonlit_dark_hours"] = moonlit_dark
        output["moon_visible_dark_fraction"] = np.divide(
            visible_dark, night, out=np.zeros_like(visible_dark), where=night > 0
        )
        output["moonlit_dark_fraction"] = np.divide(
            moonlit_dark, night, out=np.zeros_like(moonlit_dark), where=night > 0
        )
        chunks.append(output)
    return pd.concat(chunks, ignore_index=True)


def compute_lunar_illumination_table(
    h3_df: pd.DataFrame,
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
    h3_column: str = "h3",
    latitude_column: str = "centroid_lat",
    longitude_column: str = "centroid_lon",
    sample_hour_utc: int = 12,
    timezone_name: str = "America/Los_Angeles",
    timestep_minutes: int = 30,
    dark_sun_altitude_deg: float = -6.0,
    moon_altitude_min_deg: float = 0.0,
) -> pd.DataFrame:
    cells = prepare_h3_centroids(
        h3_df,
        h3_column=h3_column,
        latitude_column=latitude_column,
        longitude_column=longitude_column,
    )
    dates = compute_lunar_date_table(start_date, end_date, sample_hour_utc)
    exposure = compute_moonlight_exposure_table(
        cells,
        start_date,
        end_date,
        timezone_name,
        timestep_minutes,
        dark_sun_altitude_deg,
        moon_altitude_min_deg,
    )
    output = exposure.merge(dates, on="date", how="left", validate="many_to_one")
    output["weight_lunar_illumination"] = output["lunar_illumination_fraction"].clip(0, 1)
    output["weight_moonlit_dark_hours"] = output["moonlit_dark_fraction"].clip(0, 1)
    return output[OUTPUT_COLUMNS].copy()


__all__ = [
    "OUTPUT_COLUMNS",
    "SYNODIC_MONTH_DAYS",
    "compute_lunar_date_table",
    "compute_lunar_illumination_table",
    "compute_moonlight_exposure_table",
    "lunar_age_days_for_datetimes",
    "lunar_illumination_fraction_from_age",
    "moon_altitude_deg",
    "moon_phase_name_from_age",
    "prepare_h3_centroids",
]
