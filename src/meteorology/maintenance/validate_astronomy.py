"""Compare approximate solar/lunar methods with frozen independent USNO records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from meteorology.daylight.compute import compute_daylight_hours, solar_day_365
from meteorology.lunar.compute import (
    lunar_age_days_for_datetimes,
    lunar_illumination_fraction_from_age,
)

DAYLIGHT_MAE_LIMIT_HOURS = 0.5
DAYLIGHT_MAX_ERROR_LIMIT_HOURS = 1.0
ILLUMINATION_MAE_LIMIT = 0.1
ILLUMINATION_MAX_ERROR_LIMIT = 0.2


def _usno_daylight_hours(events: list[dict]) -> float:
    labels = {str(item.get("phen")): item.get("time") for item in events}
    if "Object continuously above the Horizon" in labels:
        return 24.0
    if "Object continuously below the Horizon" in labels:
        return 0.0
    if not labels.get("Rise") or not labels.get("Set"):
        raise ValueError("USNO record lacks a complete solar rise/set pair or polar note.")
    times = []
    for label in ("Rise", "Set"):
        match = re.match(r"^(\d{2}):(\d{2})", str(labels[label]))
        if not match:
            raise ValueError(f"USNO {label} time has an unsupported format.")
        times.append(int(match[1]) * 60 + int(match[2]))
    return ((times[1] - times[0]) % 1440) / 60.0


def _summary(errors: list[float], mae_limit: float, max_limit: float) -> dict:
    values = np.abs(np.asarray(errors, dtype=float))
    if len(values) == 0 or not np.isfinite(values).all():
        raise ValueError("Independent reference comparison has no finite errors.")
    mae = float(values.mean())
    maximum = float(values.max())
    return {
        "count": len(values), "mae": mae, "max_absolute_error": maximum,
        "mae_limit": mae_limit, "max_error_limit": max_limit,
        "passes_prespecified_limits": mae <= mae_limit and maximum <= max_limit,
    }


def compare_usno_reference(payload: dict) -> dict:
    """Return case-level evidence and prespecified aggregate threshold decisions."""

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("USNO reference has no cases.")
    seen: set[tuple[str, float, float]] = set()
    results = []
    for case in cases:
        date = pd.Timestamp(case["date"])
        latitude, longitude = float(case["latitude"]), float(case["longitude"])
        key = (date.strftime("%Y-%m-%d"), latitude, longitude)
        if key in seen:
            raise ValueError(f"Duplicate USNO reference case: {key}")
        seen.add(key)
        response = case["response"]
        coordinates = response["geometry"]["coordinates"]
        if not np.allclose(coordinates, [longitude, latitude], rtol=0, atol=1e-6):
            raise ValueError(f"USNO reference coordinates differ from case {key}.")
        data = response["properties"]["data"]
        if (int(data["year"]), int(data["month"]), int(data["day"])) != (
            date.year, date.month, date.day
        ):
            raise ValueError(f"USNO reference date differs from case {key}.")
        observed_hours = _usno_daylight_hours(data["sundata"])
        modeled_hours = compute_daylight_hours(latitude, solar_day_365(date.month, date.day))
        observed_illumination = float(str(data["fracillum"]).rstrip("%")) / 100.0
        if not 0 <= observed_illumination <= 1:
            raise ValueError(f"USNO illumination is outside [0,1] for case {key}.")
        local_noon = pd.Timestamp(f"{key[0]}T12:00:00").tz_localize(
            ZoneInfo(case["timezone"])
        )
        product_sample = pd.Timestamp(date.date(), tz="UTC") + pd.Timedelta(hours=12)
        ages = lunar_age_days_for_datetimes(pd.to_datetime([local_noon, product_sample], utc=True))
        modeled_illumination = lunar_illumination_fraction_from_age(ages)
        results.append({
            "date": key[0], "latitude": latitude, "longitude": longitude,
            "stratum": "polar" if abs(latitude) >= 66.5 else "regional",
            "reference_url": case["url"], "usno_api_version": response.get("apiversion"),
            "reference_daylight_hours": observed_hours,
            "model_daylight_hours": float(modeled_hours),
            "daylight_error_hours": float(modeled_hours - observed_hours),
            "reference_illumination_at_local_noon": observed_illumination,
            "model_illumination_at_local_noon": float(modeled_illumination[0]),
            "illumination_error_at_local_noon": float(modeled_illumination[0] - observed_illumination),
            "model_illumination_at_product_12utc": float(modeled_illumination[1]),
            "product_sample_minus_reference_noon_hours": float(
                (product_sample - local_noon) / pd.Timedelta(hours=1)
            ),
        })
    daylight = _summary(
        [case["daylight_error_hours"] for case in results],
        DAYLIGHT_MAE_LIMIT_HOURS, DAYLIGHT_MAX_ERROR_LIMIT_HOURS,
    )
    illumination = _summary(
        [case["illumination_error_at_local_noon"] for case in results],
        ILLUMINATION_MAE_LIMIT, ILLUMINATION_MAX_ERROR_LIMIT,
    )
    return {
        "reference_source": payload.get("source"),
        "reference_retrieved_at_utc": payload.get("retrieved_at_utc"),
        "comparison_method": "usno-rise-set-and-local-noon-illumination-v1",
        "daylight_hours": daylight, "lunar_illumination_fraction": illumination,
        "cases": results,
        "limitations": [
            "USNO rise/set uses apparent-horizon conventions; toolkit daylight uses geometric declination.",
            "USNO lunar illumination is rounded to a whole percent at local noon.",
            "The lunar product samples 12 UTC, which differs from USNO local noon; the matched-noon comparison isolates the phase approximation.",
            "Ten dates at two coordinates do not establish regional or multiyear accuracy.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    raw = args.reference.read_bytes()
    report = compare_usno_reference(json.loads(raw))
    report["reference_sha256"] = hashlib.sha256(raw).hexdigest()
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    else:
        print(serialized, end="")
    return 0 if all(
        report[name]["passes_prespecified_limits"]
        for name in ("daylight_hours", "lunar_illumination_fraction")
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
