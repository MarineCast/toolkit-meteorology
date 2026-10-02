"""Compare frozen hourly HRRR point samples with NDBC historical buoy observations."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

NDBC_FIELDS = {
    "ATMP": (-80.0, 60.0), "PRES": (700.0, 1200.0),
    "WSPD": (0.0, 100.0), "GST": (0.0, 100.0), "WDIR": (0.0, 360.0),
}
# NDBC standard meteorological text format, not a universal numeric replacement rule.
# https://www.ndbc.noaa.gov/faq/measdes.shtml
NDBC_MISSING = {
    "historical_stdmet": {
        "ATMP": {"999.0"}, "PRES": {"9999.0"},
        "WSPD": {"99.0"}, "GST": {"99.0"}, "WDIR": {"999"},
    },
    "realtime_stdmet": {field: {"MM"} for field in NDBC_FIELDS},
}


def _metric(pairs: list[tuple[float, float]]) -> dict:
    if not pairs:
        return {"count": 0, "bias": None, "mae": None, "rmse": None}
    values = np.asarray(pairs, dtype=float)
    errors = values[:, 0] - values[:, 1]
    return {
        "count": len(pairs), "bias": float(errors.mean()),
        "mae": float(np.abs(errors).mean()), "rmse": float(np.sqrt(np.mean(errors**2))),
    }


def _observations(
    text: str, date: str, *, source_format: str = "historical_stdmet",
    rejected: list[dict] | None = None,
) -> dict[str, dict[str, float | None]]:
    if source_format not in NDBC_MISSING:
        raise ValueError(f"Unsupported NDBC source format: {source_format}")
    lines = text.splitlines()
    if len(lines) < 3 or not lines[0].startswith("#YY"):
        raise ValueError("NDBC standard meteorological header is missing.")
    columns = lines[0].lstrip("#").split()
    result: dict[str, dict[str, float | None]] = {}
    for line in lines[2:]:
        parts = line.split()
        if len(parts) != len(columns):
            raise ValueError("NDBC observation row does not match its header.")
        values = dict(zip(columns, parts, strict=True))
        stamp = pd.Timestamp(
            f"{values['YY']}-{values['MM']}-{values['DD']}T{values['hh']}:{values['mm']}:00Z"
        )
        if stamp.strftime("%Y-%m-%d") != date or stamp.minute != 0:
            if rejected is not None:
                rejected.append({"valid_time_utc": stamp.isoformat(), "field": None,
                                 "raw_token": None, "disposition": "excluded",
                                 "reason": "out_of_period_or_not_exact_hour"})
            continue
        key = stamp.isoformat()
        if key in result:
            raise ValueError(f"Duplicate NDBC hourly observation: {key}")
        parsed: dict[str, float | None] = {}
        for field, (lower, upper) in NDBC_FIELDS.items():
            token = values[field]
            reason = None
            if token in NDBC_MISSING[source_format][field]:
                reason = "source_missing_token"
            else:
                try:
                    value = float(token)
                except ValueError:
                    reason = "malformed_token"
                else:
                    if not np.isfinite(value):
                        reason = "non_finite"
                    elif not lower <= value <= upper:
                        reason = "outside_physical_screen"
            parsed[field] = None if reason else value
            if reason and rejected is not None:
                rejected.append({"valid_time_utc": key, "field": field,
                                 "raw_token": token, "disposition": "rejected", "reason": reason})
        result[key] = parsed
    return result


def compare_ndbc_day(
    observation_text: str, model_rows: list[dict], *, date: str, station_metadata: dict,
    source_format: str = "historical_stdmet",
) -> dict:
    """Align exact UTC hours and report descriptive point-level errors only."""

    rejected: list[dict] = []
    observations = _observations(observation_text, date, source_format=source_format,
                                 rejected=rejected)
    models: dict[str, dict] = {}
    for row in model_rows:
        stamp = pd.Timestamp(row["valid_time_utc"])
        if stamp.tzinfo is None or stamp.utcoffset() != pd.Timedelta(0):
            raise ValueError("HRRR pilot time must be UTC-aware.")
        key = stamp.isoformat()
        if key in models:
            raise ValueError(f"Duplicate HRRR pilot time: {key}")
        models[key] = row
    expected = [stamp.isoformat() for stamp in pd.date_range(date, periods=24, freq="h", tz="UTC")]
    aligned = []
    pairs: dict[str, list[tuple[float, float]]] = {
        "temperature_2m_c": [], "mean_sea_level_pressure_hpa": [],
        "wind_speed_ms": [], "wind_gust_ms": [],
    }
    direction_errors: list[float] = []
    for key in expected:
        observed, modeled = observations.get(key), models.get(key)
        if observed is None or modeled is None:
            continue
        speed = float(np.hypot(modeled["u_wind_10m_ms"], modeled["v_wind_10m_ms"]))
        candidates = {
            "temperature_2m_c": (float(modeled["temperature_2m_k"]) - 273.15, observed["ATMP"]),
            "mean_sea_level_pressure_hpa": (
                float(modeled["mean_sea_level_pressure_pa"]) / 100.0, observed["PRES"]
            ),
            "wind_speed_ms": (speed, observed["WSPD"]),
            "wind_gust_ms": (float(modeled["wind_gust_surface_ms"]), observed["GST"]),
        }
        accepted = {}
        for field, (prediction, reference) in candidates.items():
            if reference is not None and np.isfinite(prediction):
                pairs[field].append((prediction, reference))
                accepted[field] = {"model": prediction, "reference": reference}
        if observed["WDIR"] is not None and observed["WSPD"] is not None and min(
            speed, observed["WSPD"]
        ) >= 1.0:
            direction = float(np.degrees(np.arctan2(
                -float(modeled["u_wind_10m_ms"]), -float(modeled["v_wind_10m_ms"])
            )) % 360.0)
            difference = abs((direction - observed["WDIR"] + 180.0) % 360.0 - 180.0)
            direction_errors.append(difference)
            accepted["wind_direction_deg"] = {
                "model": direction, "reference": observed["WDIR"],
                "absolute_circular_error": difference,
            }
        aligned.append({
            "valid_time_utc": key, "source_grid_distance_m": modeled["source_grid_distance_m"],
            "values": accepted,
        })
    metrics = {name: _metric(values) for name, values in pairs.items()}
    metrics["wind_direction_absolute_error_deg"] = {
        "count": len(direction_errors),
        "mean": float(np.mean(direction_errors)) if direction_errors else None,
        "max": float(np.max(direction_errors)) if direction_errors else None,
        "calm_cutoff_ms": 1.0,
    }
    for item in rejected:
        item["station"] = station_metadata["station"]
        item["source_url"] = station_metadata.get("source_url")
    return {
        "date_utc": date, "station": station_metadata["station"],
        "source_format": source_format, "rejected_records": rejected,
        "station_metadata": station_metadata,
        "expected_hours": 24, "observation_hours": len(observations),
        "model_hours": len(models), "matched_hours": len(aligned),
        "metrics": metrics, "matched_records": aligned,
        "threshold_decision": "not_set_for_one_station_day",
        "limitations": [
            "NDBC is a point buoy; HRRR is the nearest 3 km grid point, not an H3 cell mean.",
            "Buoy wind is measured at the documented 3.8 m sensor height; HRRR wind is 10 m and is not height-adjusted here.",
            "Buoy air temperature is measured at the documented 3.4 m sensor height; HRRR temperature is 2 m.",
            "NDBC wind represents a measurement period; HRRR fields are analysis valid-time snapshots.",
            "One buoy on one day is a diagnostic pilot, not regional or seasonal validation.",
            "Raw HRRR GRIB bytes are not retained by this pilot; source object URIs are retained with scalar samples.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--station-metadata", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--source-format", choices=tuple(NDBC_MISSING),
                        default="historical_stdmet")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    observed_bytes, model_bytes = args.observations.read_bytes(), args.model.read_bytes()
    report = compare_ndbc_day(
        observed_bytes.decode(), json.loads(model_bytes), date=args.date,
        station_metadata=json.loads(args.station_metadata.read_text()),
        source_format=args.source_format,
    )
    report["observation_slice_sha256"] = hashlib.sha256(observed_bytes).hexdigest()
    report["model_sample_sha256"] = hashlib.sha256(model_bytes).hexdigest()
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
