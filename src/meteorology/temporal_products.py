"""Hourly time and window contracts for candidate atmospheric products.

These functions validate already decoded source records. They perform no acquisition
and do not confer source compatibility or empirical accuracy on those records.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo


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
    as_of_utc: str | None = None,
) -> dict:
    """Gate a complete candidate day of distinct source-native hourly analyses."""
    expected = hourly_utc_instants(local_date, timezone_name)
    as_of = _utc(as_of_utc) if as_of_utc is not None else None
    seen: set[datetime] = set()
    for record in records:
        valid = _utc(record["valid_time_utc"])
        initialized = _utc(record["init_time_utc"])
        available = _utc(record["available_at_utc"])
        if as_of is not None and available > as_of:
            raise ValueError("Hourly source value was unavailable at the requested as-of time.")
        if valid in seen:
            raise ValueError(f"Duplicate hourly valid time: {valid.isoformat()}")
        seen.add(valid)
        if initialized != valid or record.get("forecast_hour") != 0:
            raise ValueError("Hourly analysis requires its own f00 cycle; forecast vintages need a separate product.")
        if record.get("source_model") != "HRRR" or record.get("source_product") != "sfc":
            raise ValueError("Hourly source identity is not the declared HRRR sfc analysis.")
        for field in required_fields:
            value = record.get(field)
            if value is None or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"Required hourly core field {field} is missing or non-finite at {valid}.")
    if seen != set(expected):
        raise ValueError(f"Hourly local day is incomplete: expected {len(expected)}, observed {len(seen)}; missing={len(set(expected) - seen)}, extra={len(seen - set(expected))}.")
    return {"local_date": local_date, "timezone": timezone_name,
            "expected_hours": len(expected), "observed_hours": len(seen),
            "coverage_fraction": 1.0, "status": "COMPLETE"}


def summarize_hourly_window(
    records: list[dict], *, start_utc: str, end_utc: str, field: str,
    as_of_utc: str | None = None,
) -> dict:
    """Summarize start-of-hour samples without filling unknown one-hour slots."""
    start, end = _utc(start_utc), _utc(end_utc)
    duration = (end - start).total_seconds() / 3600
    if duration <= 0 or duration != int(duration) or start.minute or end.minute:
        raise ValueError("Hourly windows require positive whole-hour UTC boundaries.")
    as_of = _utc(as_of_utc) if as_of_utc is not None else None
    by_time: dict[datetime, float | None] = {}
    for row in records:
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

