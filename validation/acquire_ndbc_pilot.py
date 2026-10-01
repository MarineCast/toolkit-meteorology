"""Reproduce one bounded day of hourly HRRR scalar samples at NDBC 46088.

Requires the toolkit's ``acquisition`` extra and network access. GRIB messages
are selected by the toolkit's strict decoder and retained only in temporary
storage; the output contains scalar samples and retrieval URIs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from sklearn.neighbors import BallTree

from meteorology.config import load_meteorological_config
from meteorology.surface_weather.sampling import EARTH_RADIUS_M
from meteorology.surface_weather.source import (
    HRRR_VARIABLES,
    _combined_search,
    fetch_cropped_hrrr_fields,
    hrrr_aws_archive_uri,
    normalize_hrrr_values,
)

STATION_LAT = 48.332
STATION_LON = -123.179
VARIABLE_NAMES = (
    "temperature_2m_k", "u_wind_10m_ms", "v_wind_10m_ms",
    "wind_gust_surface_ms", "mean_sea_level_pressure_pa",
)
VARIABLES = {name: HRRR_VARIABLES[name] for name in VARIABLE_NAMES}


def _indexed_bytes(valid_times: list[pd.Timestamp]) -> int:
    expression = re.compile(_combined_search(VARIABLES))
    total = 0
    for valid in valid_times:
        uri = hrrr_aws_archive_uri(valid)
        index = requests.get(uri + ".idx", timeout=30)
        index.raise_for_status()
        head = requests.head(uri, timeout=30)
        head.raise_for_status()
        object_size = int(head.headers["Content-Length"])
        rows = [
            (int(parts[1]), line)
            for line in index.text.splitlines()
            if len(parts := line.split(":", 2)) > 2
        ]
        matches = [
            (rows[i + 1][0] if i + 1 < len(rows) else object_size) - offset
            for i, (offset, line) in enumerate(rows) if expression.search(line)
        ]
        if len(matches) != len(VARIABLES):
            raise ValueError(f"HRRR index has {len(matches)} selected fields for {valid}.")
        total += sum(matches)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/data/environment_meteorological.yaml")
    parser.add_argument("--date", default="2024-01-02", help="UTC date to sample")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-indexed-bytes", type=int, default=1_000_000_000)
    args = parser.parse_args()
    config = load_meteorological_config(args.config)
    valid_times = list(pd.date_range(args.date, periods=24, freq="h", tz="UTC"))
    estimated_bytes = _indexed_bytes(valid_times)
    print(f"Indexed selected GRIB bytes: {estimated_bytes}", flush=True)
    if estimated_bytes > args.max_indexed_bytes:
        raise ValueError("Selected GRIB ranges exceed the pilot download budget.")
    rows = json.loads(args.output.read_text()) if args.output.exists() else []
    existing = {row["valid_time_utc"] for row in rows}
    for valid in valid_times:
        if valid.isoformat() in existing:
            continue
        flat, units, uri = fetch_cropped_hrrr_fields(
            valid_time_utc=valid, bbox=config.bbox,
            bbox_padding_degrees=config.surface_weather.bbox_padding_degrees,
            availability_lag_hours=config.surface_weather.availability_lag_hours,
            variables=VARIABLES,
        )
        tree = BallTree(
            np.deg2rad(flat[["hrrr_lat", "hrrr_lon"]].to_numpy(dtype=float)),
            metric="haversine",
        )
        distance, index = tree.query(np.deg2rad([[STATION_LAT, STATION_LON]]), k=1)
        chosen = int(index[0, 0])
        record = {
            "valid_time_utc": valid.isoformat(), "source_uri": uri,
            "source_grid_distance_m": float(distance[0, 0] * EARTH_RADIUS_M),
            "source_lat": float(flat["hrrr_lat"].iloc[chosen]),
            "source_lon": float(flat["hrrr_lon"].iloc[chosen]),
            "source_wind_basis": flat.attrs.get("source_wind_basis"),
        }
        for name in VARIABLE_NAMES:
            record[name] = float(normalize_hrrr_values(
                flat[name].iloc[[chosen]], variable=name, units=units[name]
            ).iloc[0])
        rows.append(record)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(sorted(rows, key=lambda row: row["valid_time_utc"]), indent=2) + "\n")
        os.replace(temporary, args.output)
        print(f"Sampled {valid.isoformat()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
