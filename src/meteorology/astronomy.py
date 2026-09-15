"""Shared dependency-light solar astronomy for daylight and lunar products."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _wrap_degrees(value: np.ndarray | float) -> np.ndarray | float:
    return np.mod(value, 360.0)


def julian_day(datetimes_utc: pd.DatetimeIndex) -> np.ndarray:
    values = pd.DatetimeIndex(pd.to_datetime(datetimes_utc, utc=True))
    unix_ns = (
        values.tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]").astype("int64")
    )
    return (unix_ns / 1_000_000_000.0) / 86400.0 + 2440587.5


def altitude_from_ra_dec(
    *,
    jd: float,
    ra_deg: float,
    dec_deg: float,
    lat_deg: np.ndarray,
    lon_deg: np.ndarray,
) -> np.ndarray:
    gmst_deg = _wrap_degrees(280.46061837 + 360.98564736629 * (jd - 2451545.0))
    hour_angle = _wrap_degrees(_wrap_degrees(gmst_deg + lon_deg) - ra_deg)
    hour_angle = np.where(hour_angle > 180.0, hour_angle - 360.0, hour_angle)
    lat_rad = np.deg2rad(lat_deg)
    dec_rad = math.radians(dec_deg)
    ha_rad = np.deg2rad(hour_angle)
    altitude = np.arcsin(
        np.sin(lat_rad) * math.sin(dec_rad) + np.cos(lat_rad) * math.cos(dec_rad) * np.cos(ha_rad)
    )
    return np.rad2deg(altitude)


def solar_altitude_deg(
    timestamp_utc: pd.Timestamp,
    lat_deg: np.ndarray,
    lon_deg: np.ndarray,
) -> np.ndarray:
    """Return approximate geometric solar elevation for observer coordinates."""

    jd = float(julian_day(pd.DatetimeIndex([timestamp_utc]))[0])
    days = jd - 2451545.0
    mean_lon = float(_wrap_degrees(280.460 + 0.9856474 * days))
    mean_anomaly = float(_wrap_degrees(357.528 + 0.9856003 * days))
    anomaly_rad = math.radians(mean_anomaly)
    ecliptic_lon = float(
        _wrap_degrees(
            mean_lon + 1.915 * math.sin(anomaly_rad) + 0.020 * math.sin(2.0 * anomaly_rad)
        )
    )
    obliquity = 23.439 - 0.0000004 * days
    longitude_rad = math.radians(ecliptic_lon)
    obliquity_rad = math.radians(obliquity)
    ra_rad = math.atan2(math.cos(obliquity_rad) * math.sin(longitude_rad), math.cos(longitude_rad))
    dec_rad = math.asin(math.sin(obliquity_rad) * math.sin(longitude_rad))
    return altitude_from_ra_dec(
        jd=jd,
        ra_deg=math.degrees(ra_rad) % 360.0,
        dec_deg=math.degrees(dec_rad),
        lat_deg=np.asarray(lat_deg, dtype="float64"),
        lon_deg=np.asarray(lon_deg, dtype="float64"),
    )


def local_day_sample_times_utc(
    date: str | pd.Timestamp,
    timezone_name: str,
    timestep_minutes: int,
) -> pd.DatetimeIndex:
    """Sample one local civil day while preserving 23- and 25-hour DST days."""

    if timestep_minutes <= 0 or timestep_minutes > 1440 or 60 % timestep_minutes:
        raise ValueError("timestep_minutes must be positive, at most 1440, and divide 60.")
    day = pd.Timestamp(date).normalize()
    local_start = day.tz_localize(timezone_name)
    local_end = (day + pd.Timedelta(days=1)).tz_localize(timezone_name)
    return pd.date_range(
        start=local_start,
        end=local_end,
        freq=f"{int(timestep_minutes)}min",
        inclusive="left",
    ).tz_convert("UTC")


def solar_profile_metrics(
    *,
    date: str | pd.Timestamp,
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    timezone_name: str,
    timestep_minutes: int,
    low_sun_max_degrees: float = 10.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return maximum, daylight mean, and low-sun daylight hours per coordinate."""

    latitudes = np.asarray(latitudes, dtype="float64")
    longitudes = np.asarray(longitudes, dtype="float64")
    if latitudes.shape != longitudes.shape:
        raise ValueError("Latitude and longitude arrays must have the same shape.")
    samples = local_day_sample_times_utc(date, timezone_name, timestep_minutes)
    elevations = np.vstack(
        [solar_altitude_deg(timestamp, latitudes, longitudes) for timestamp in samples]
    )
    daylight = elevations > 0.0
    maximum = np.max(elevations, axis=0)
    daylight_sum = np.where(daylight, elevations, 0.0).sum(axis=0)
    daylight_count = daylight.sum(axis=0)
    daylight_mean = np.divide(
        daylight_sum,
        daylight_count,
        out=np.full(latitudes.shape, np.nan, dtype="float64"),
        where=daylight_count > 0,
    )
    low_sun = daylight & (elevations < float(low_sun_max_degrees))
    low_sun_hours = low_sun.sum(axis=0).astype("float64") * (timestep_minutes / 60.0)
    return maximum, daylight_mean, low_sun_hours
