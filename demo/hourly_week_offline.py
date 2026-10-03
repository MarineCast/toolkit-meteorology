"""Run a seven-local-day synthetic hourly-weather demo in a fresh workspace.

This script makes zero provider requests. Its normalized grids are test inputs,
not HRRR observations or evidence of provider compatibility.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import box

from meteorology.artifacts import checksum_path
from meteorology.cli import initialize_workspace
from meteorology.config import load_meteorological_config
from meteorology.hourly_weather.product import (
    acquire_hourly_weather, build_hourly_weather, retain_decoded_hourly_grid,
)
from meteorology.releases import freeze_release
from meteorology.spatial_support.build import (
    build_meteorological_spatial_support, load_meteorological_support,
)
from meteorology.surface_weather.source import _grid_hash
from meteorology.temporal_products import hourly_utc_instants, summarize_hourly_window
from meteorology.validation import validate_product


def run(start_date: str, workspace: Path, output: Path) -> dict:
    workspace = workspace.resolve()
    output = output.resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError(f"Demo workspace must be fresh: {workspace}")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Demo output must be fresh: {output}")
    os.environ["METEOROLOGY_WORKSPACE"] = str(workspace)
    initialize_workspace(workspace)
    config_path = workspace / "config/data/environment_meteorological.yaml"
    build_meteorological_spatial_support(config_path)
    config = load_meteorological_config(config_path)
    support = load_meteorological_support(5, config_path)
    grid_hash = _grid_hash(support["CENTROID_LAT"], support["CENTROID_LON"])
    footprint = box(config.bbox["min_lon"] - 0.1, config.bbox["min_lat"] - 0.1,
                    config.bbox["max_lon"] + 0.1, config.bbox["max_lat"] + 0.1)
    decoded = workspace / "synthetic-decoded"
    decoded.mkdir()
    first = date.fromisoformat(start_date)
    days = [(first + timedelta(days=offset)).isoformat() for offset in range(7)]
    all_times = [valid for day in days for valid in hourly_utc_instants(
        day, config.surface_weather.timezone)]
    for index, valid in enumerate(all_times):
        frame = pd.DataFrame({
            "SOURCE_GRID_INDEX": np.arange(len(support), dtype="int32"),
            "SOURCE_LAT": support["CENTROID_LAT"].to_numpy(dtype=float),
            "SOURCE_LON": support["CENTROID_LON"].to_numpy(dtype=float),
            "VALID_TIME_UTC": valid.isoformat(), "INIT_TIME_UTC": valid.isoformat(),
            "AVAILABLE_AT_UTC": (valid + timedelta(hours=6)).isoformat(),
            "SOURCE_MODEL": "synthetic_hrrr", "SOURCE_PRODUCT": "sfc",
            "FORECAST_HOUR": 0, "SOURCE_GRID_HASH": grid_hash,
            "SOURCE_WIND_BASIS": "earth_relative",
            "TEMPERATURE_2M_K": 273.15 + 5.0 + index / 24,
            "RELATIVE_HUMIDITY_2M_PCT": 60.0,
            "U_WIND_10M_MS": 0.0, "V_WIND_10M_MS": 0.0,
            "WIND_GUST_SURFACE_MS": 0.0, "VISIBILITY_M": 15_000.0,
            "TOTAL_CLOUD_COVER_PCT": 0.0, "PRECIP_RATE_KG_M2_S": 0.0,
            "MEAN_SEA_LEVEL_PRESSURE_PA": 101_000.0,
        })
        frame.attrs["native_footprint"] = footprint
        frame.attrs["max_nearest_distance_m"] = 1000.0
        retain_decoded_hourly_grid(
            frame, valid_time_utc=valid.isoformat(),
            source_uri=f"synthetic://fixture/{valid:%Y%m%dT%HZ}",
            retrieved_at_utc=(valid + timedelta(hours=1)).isoformat(),
            output_dir=decoded, source_evidence_kind="synthetic_fixture")

    output.mkdir(parents=True)
    releases = output / "frozen-releases"
    tables = []
    daily = []
    for day in days:
        preview = acquire_hourly_weather(config_path, local_date=day,
                                         decoded_dir=decoded, dry_run=True)
        if not preview["complete_input_files"] or preview["source_requests"]:
            raise ValueError(f"Unexpected input/network preview for {day}")
        acquisition = acquire_hourly_weather(config_path, local_date=day, decoded_dir=decoded)
        acquisition_gate = validate_product(acquisition["manifest"])
        product_manifest = build_hourly_weather(config_path)
        product_gate = validate_product(product_manifest)
        frozen = freeze_release(product_manifest, releases)
        frozen_gate = validate_product(frozen)
        published = workspace / ("data/processed/domain/environmental_layer/"
                                 "meteorological/hourly_weather/H3_HOURLY_WEATHER_RES_5")
        table = pq.read_table(published / f"date={day}" / "part-000.parquet")
        tables.append(table)
        daily.append(dict(date=day, hours=preview["expected_cycles"],
                          rows=table.num_rows, acquisition_valid=acquisition_gate["valid"],
                          product_valid=product_gate["valid"],
                          frozen_valid=frozen_gate["valid"],
                          product_manifest_sha256=checksum_path(product_manifest),
                          frozen_manifest=frozen.relative_to(output).as_posix()))
    weekly = pa.concat_tables(tables)
    if weekly.num_rows != len(all_times) * len(support):
        raise ValueError("Weekly row count does not cover every hour and support cell.")
    week_path = output / "hourly_weather_synthetic_week.parquet"
    pq.write_table(weekly, week_path)
    records = weekly.to_pandas()
    cell = str(records["H3_INDEX"].iloc[0])
    selected = records.loc[records["H3_INDEX"] == cell].to_dict("records")
    summary = summarize_hourly_window(
        selected, start_utc=all_times[0].isoformat(),
        end_utc=(all_times[-1] + timedelta(hours=1)).isoformat(),
        field="TEMPERATURE_2M_C", h3_index=cell)
    report = dict(source_evidence_kind="synthetic_fixture", provider_requests=0,
                  network_bytes=0, start_date=days[0], end_date=days[-1],
                  timezone=config.surface_weather.timezone, utc_hours=len(all_times),
                  h3_cells=len(support), weekly_rows=weekly.num_rows,
                  combined_artifact="hourly_weather_synthetic_week.parquet",
                  combined_sha256=checksum_path(week_path),
                  combined_is_demo_aggregation_not_producer_release=True,
                  daily=daily, selected_cell=cell, selected_cell_summary=summary,
                  real_source_compatibility="NOT_RUN",
                  empirical_scientific_acceptance="NOT_RUN")
    (output / "week_demo_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2024-01-02")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.start_date, args.workspace, args.output), indent=2))


if __name__ == "__main__":
    main()
