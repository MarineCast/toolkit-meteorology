"""Hourly time and window contracts for candidate atmospheric products.

These functions validate already decoded source records. They perform no acquisition
and do not confer source compatibility or empirical accuracy on those records.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

from .surface_weather.sampling import AVAILABILITY_POLICY


def _utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("Source times must be explicit UTC instants.")
    return stamp.astimezone(timezone.utc)


def local_day_bounds(local_date: str, timezone_name: str) -> tuple[datetime, datetime]:
    day = date.fromisoformat(local_date)
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    return start, end


def hourly_utc_instants(local_date: str, timezone_name: str) -> list[datetime]:
    """Return unique true UTC hourly instants for a local civil day (23/24/25)."""
    start, end = local_day_bounds(local_date, timezone_name)
    if start.minute or start.second or end.minute or end.second:
        raise ValueError("Timezone does not align local-day boundaries to UTC hours.")
    hours = int((end - start).total_seconds() // 3600)
    if hours not in {23, 24, 25}:
        raise ValueError("Unsupported local civil-day duration for hourly product.")
    return [start + timedelta(hours=index) for index in range(hours)]


HOURLY_CORE = (
    "TEMPERATURE_2M_C", "RELATIVE_HUMIDITY_2M_PCT", "U_WIND_10M_MS",
    "V_WIND_10M_MS", "WIND_SPEED_10M_MS", "WIND_GUST_SURFACE_MS",
    "VISIBILITY_KM", "TOTAL_CLOUD_COVER_PCT", "MEAN_SEA_LEVEL_PRESSURE_HPA",
)


def validate_hourly_records(
    records: list[dict], *, local_date: str, timezone_name: str,
    required_fields: tuple[str, ...] = HOURLY_CORE,
    expected_h3_cells: set[str] | None = None,
    optional_fields: tuple[str, ...] = (),
    availability_lag_hours: int | None = None,
    expected_source_model: str = "HRRR",
    as_of_utc: str | None = None,
) -> dict:
    """Gate a complete candidate day of distinct source-native hourly analyses.

    When H3 cells are supplied, every expected (UTC hour, cell) must occur once.
    Optional fields report coverage without changing core completeness.
    """
    expected = hourly_utc_instants(local_date, timezone_name)
    if availability_lag_hours is not None and availability_lag_hours < 0:
        raise ValueError("Availability lag must be nonnegative.")
    as_of = _utc(as_of_utc) if as_of_utc is not None else None
    if expected_h3_cells is not None and not expected_h3_cells:
        raise ValueError("Hourly H3 support cannot be empty.")
    seen: set[tuple[datetime, str | None]] = set()
    optional_counts = {field: 0 for field in optional_fields}
    for record in records:
        valid = _utc(record["valid_time_utc"])
        initialized = _utc(record["init_time_utc"])
        available = _utc(record["available_at_utc"])
        if record.get("availability_policy") != AVAILABILITY_POLICY:
            raise ValueError("Unknown or missing hourly availability policy.")
        if available < valid or (availability_lag_hours is not None
                                 and available != valid + timedelta(hours=availability_lag_hours)):
            raise ValueError("Hourly assumed availability disagrees with valid time and lag policy.")
        if as_of is not None and available > as_of:
            raise ValueError("Hourly source value was unavailable at the requested as-of time.")
        cell = record.get("H3_INDEX") if expected_h3_cells is not None else None
        if expected_h3_cells is not None and cell not in expected_h3_cells:
            raise ValueError(f"Unexpected or missing H3 cell at {valid.isoformat()}: {cell!r}")
        key = (valid, cell)
        if key in seen:
            raise ValueError(f"Duplicate hourly time/cell: {valid.isoformat()}, {cell!r}")
        seen.add(key)
        if initialized != valid or record.get("forecast_hour") != 0:
            raise ValueError("Hourly analysis requires its own f00 cycle; forecast vintages need a separate product.")
        if record.get("source_model") != expected_source_model or record.get("source_product") != "sfc":
            raise ValueError("Hourly source identity is not the declared HRRR sfc analysis.")
        for field in required_fields:
            value = record.get(field)
            if value is None or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"Required hourly core field {field} is missing or non-finite at {valid}.")
        for field in optional_fields:
            value = record.get(field)
            if isinstance(value, (int, float)) and isfinite(value):
                optional_counts[field] += 1
    expected_keys = {(instant, cell) for instant in expected
                     for cell in (expected_h3_cells if expected_h3_cells is not None else {None})}
    if seen != expected_keys:
        raise ValueError(f"Hourly local day is incomplete: expected {len(expected_keys)}, observed {len(seen)}; missing={len(expected_keys - seen)}, extra={len(seen - expected_keys)}.")
    return {"local_date": local_date, "timezone": timezone_name,
            "expected_hours": len(expected), "observed_hours": len({key[0] for key in seen}),
            "expected_cells": len(expected_h3_cells) if expected_h3_cells is not None else None,
            "expected_rows": len(expected_keys), "observed_rows": len(seen),
            "coverage_fraction": 1.0, "status": "COMPLETE",
            "optional_field_coverage": {field: count / len(expected_keys)
                                        for field, count in optional_counts.items()}}


def summarize_hourly_window(
    records: list[dict], *, start_utc: str, end_utc: str, field: str,
    h3_index: str | None = None,
    as_of_utc: str | None = None,
) -> dict:
    """Summarize start-of-hour samples without filling unknown one-hour slots."""
    start, end = _utc(start_utc), _utc(end_utc)
    duration = (end - start).total_seconds() / 3600
    if (duration <= 0 or duration != int(duration)
            or any((stamp.minute, stamp.second, stamp.microsecond) != (0, 0, 0)
                   for stamp in (start, end))):
        raise ValueError("Hourly windows require positive whole-hour UTC boundaries.")
    as_of = _utc(as_of_utc) if as_of_utc is not None else None
    by_time: dict[datetime, float | None] = {}
    for row in records:
        if h3_index is not None and row.get("H3_INDEX") != h3_index:
            continue
        if h3_index is None and "H3_INDEX" in row:
            raise ValueError("Select an H3 cell before summarizing a multi-cell hourly window.")
        valid = _utc(row["valid_time_utc"])
        if not start <= valid < end:
            continue
        if valid in by_time:
            raise ValueError(f"Duplicate hourly window time: {valid.isoformat()}")
        value = row.get(field)
        available = _utc(row["available_at_utc"])
        by_time[valid] = (
            float(value) if isinstance(value, (int, float)) and isfinite(value)
            and (as_of is None or available <= as_of) else None
        )
    expected = [start + timedelta(hours=offset) for offset in range(int(duration))]
    values = [by_time[instant] for instant in expected if by_time.get(instant) is not None]
    count = len(values)
    return {
        "field": field, "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "support_convention": "instantaneous_start_of_hour_represents_following_hour_for_duration_only",
        "expected_hours": len(expected), "valid_hours": count,
        "coverage_fraction": count / len(expected),
        "sampled_min": min(values) if values else None,
        "sampled_max": max(values) if values else None,
        "sampled_mean": sum(values) / count if count else None,
    }
