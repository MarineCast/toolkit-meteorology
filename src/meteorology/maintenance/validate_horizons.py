"""Compare solar and lunar altitude approximations with frozen JPL Horizons data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pandas as pd

from meteorology.astronomy import solar_altitude_deg
from meteorology.lunar.compute import moon_altitude_deg

LIMITS_DEG = {
    "sun": {"mae": 2.0, "max_absolute_error": 5.0},
    "moon": {"mae": 6.0, "max_absolute_error": 12.0},
}


def compare_horizons_reference(payload: dict) -> dict:
    """Validate frozen query identity and compare exact UTC airless altitudes."""

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Horizons reference has no cases.")
    seen: set[tuple[str, str, float, float]] = set()
    errors: dict[str, list[float]] = {"sun": [], "moon": []}
    results = []
    for case in cases:
        body = case["body"]
        if body not in errors:
            raise ValueError(f"Unknown Horizons body: {body}")
        date = pd.Timestamp(case["date"]).strftime("%Y-%m-%d")
        latitude, longitude = float(case["latitude"]), float(case["longitude"])
        key = (body, date, latitude, longitude)
        if key in seen:
            raise ValueError(f"Duplicate Horizons reference case: {key}")
        seen.add(key)
        url = urlsplit(case["url"])
        query = {name: values[0].strip("'") for name, values in parse_qs(url.query).items()}
        expected_body = "10" if body == "sun" else "301"
        if (
            url.netloc != "ssd.jpl.nasa.gov"
            or query.get("COMMAND") != expected_body
            or query.get("QUANTITIES") != "4"
            or query.get("APPARENT") != "AIRLESS"
            or query.get("TIME_TYPE") != "UT"
            or query.get("START_TIME") != f"{date} 00:00"
            or query.get("STOP_TIME") != f"{date} 23:00"
            or query.get("SITE_COORD") != f"{longitude},{latitude},0"
        ):
            raise ValueError(f"Horizons request identity differs from case {key}.")
        rows = case["rows"]
        expected = list(pd.date_range(date, periods=24, freq="h", tz="UTC"))
        observed_times = [pd.Timestamp(row["timestamp_utc"]) for row in rows]
        if observed_times != expected:
            raise ValueError(f"Horizons UTC hourly coverage differs for case {key}.")
        comparison = []
        for timestamp, row in zip(expected, rows, strict=True):
            reference = float(row["elevation_deg"])
            if not -90 <= reference <= 90:
                raise ValueError(f"Horizons altitude is invalid for {timestamp}.")
            args = (timestamp, np.array([latitude]), np.array([longitude]))
            estimate = float((solar_altitude_deg if body == "sun" else moon_altitude_deg)(*args)[0])
            difference = estimate - reference
            errors[body].append(difference)
            comparison.append({
                "timestamp_utc": timestamp.isoformat(),
                "reference_airless_altitude_deg": reference,
                "model_altitude_deg": estimate,
                "error_deg": difference,
            })
        results.append({
            "body": body, "date": date, "latitude": latitude, "longitude": longitude,
            "reference_url": case["url"], "response_sha256": case["response_sha256"],
            "hours": comparison,
        })
    metrics = {}
    for body, differences in errors.items():
        if not differences:
            raise ValueError(f"Horizons reference has no {body} cases.")
        values = np.asarray(differences, dtype=float)
        mae = float(np.abs(values).mean())
        maximum = float(np.abs(values).max())
        metrics[body] = {
            "count": len(values), "bias_deg": float(values.mean()),
            "mae_deg": mae, "max_absolute_error_deg": maximum,
            "prespecified_limits_deg": LIMITS_DEG[body],
            "passes_prespecified_limits": (
                mae <= LIMITS_DEG[body]["mae"]
                and maximum <= LIMITS_DEG[body]["max_absolute_error"]
            ),
        }
    paired: dict[tuple[str, float, float], dict[str, dict]] = {}
    for case in results:
        site_day = (case["date"], case["latitude"], case["longitude"])
        paired.setdefault(site_day, {})[case["body"]] = case
    dark_visible = []
    for site_day, bodies in paired.items():
        if set(bodies) != {"sun", "moon"}:
            raise ValueError(f"Horizons Sun/Moon pair is incomplete for {site_day}.")
        sun, moon = bodies["sun"]["hours"], bodies["moon"]["hours"]
        reference = sum(
            s["reference_airless_altitude_deg"] < -6.0
            and m["reference_airless_altitude_deg"] > 0.0
            for s, m in zip(sun, moon, strict=True)
        )
        modeled = sum(
            s["model_altitude_deg"] < -6.0 and m["model_altitude_deg"] > 0.0
            for s, m in zip(sun, moon, strict=True)
        )
        dark_visible.append({
            "date": site_day[0], "latitude": site_day[1], "longitude": site_day[2],
            "reference_hourly_dark_visible_count": reference,
            "model_hourly_dark_visible_count": modeled,
            "hourly_count_difference": modeled - reference,
        })
    return {
        "reference_source": payload.get("source"),
        "reference_retrieved_at_utc": payload.get("retrieved_at_utc"),
        "comparison_method": "jpl-horizons-airless-altitude-hourly-v1",
        "metrics": metrics, "cases": results,
        "dark_visible_hourly": dark_visible,
        "limitations": [
            "Horizons airless apparent altitude includes high-precision effects absent from the toolkit approximations.",
            "Zero-meter geodetic observer height and no terrain horizon are assumed.",
            "Hourly samples at two locations on four regional and two polar dates do not establish multiyear regional accuracy.",
            "Dark-visible counts use hourly UTC snapshots, not the product's 30-minute local-civil-day integration.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    raw = args.reference.read_bytes()
    report = compare_horizons_reference(json.loads(raw))
    report["reference_sha256"] = hashlib.sha256(raw).hexdigest()
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    else:
        print(serialized, end="")
    return 0 if all(metric["passes_prespecified_limits"] for metric in report["metrics"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
