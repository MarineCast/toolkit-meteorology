"""Build a one-day synthetic product through the installed production builders."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pyarrow as pa

from .artifacts import cell_set_hash, checksum_path, manifest_payload, parquet_contract, write_manifest, write_table
from .config import load_meteorological_config
from .core.data.meteorological_schemas import HRRR_CROSSWALK_SCHEMA, HRRR_INVENTORY_SCHEMA, HRRR_SAMPLE_SCHEMA
from .core.config.paths import project_root
from .daily_matrix import export
from .daylight.build import build_daylight
from .lunar.build import build_lunar
from .spatial_support.build import build_meteorological_spatial_support, load_meteorological_support
from .surface_weather.build import build_surface_weather
from .surface_weather.download import _inventory_row, sample_path
from .surface_weather.sampling import build_nearest_grid_crosswalk, make_sample_times_for_local_date, sample_source_grid
from .validation import validate_daily_matrix, validate_product


def build_offline_example(config_path: str | Path = "config/data/environment_meteorological.yaml") -> dict:
    """Create explicitly synthetic inputs, then invoke the normal product APIs.

    Requires a fresh initialized workspace whose three family date ranges are
    the same single local date. Nothing is downloaded or silently overwritten.
    """

    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    date = weather.start_date
    if not (date == weather.end_date == config.daylight.start_date == config.daylight.end_date == config.lunar.start_date == config.lunar.end_date):
        raise ValueError("Offline example requires the same explicit one-day range in weather, daylight and lunar configuration.")
    destinations = (config.support_manifest_path, weather.inventory_path, weather.acquisition_manifest_path,
                    weather.manifest_path, config.daylight.manifest_path, config.lunar.manifest_path)
    if any(path.exists() for path in destinations):
        raise FileExistsError("Offline example requires a fresh workspace; one or more output artifacts already exist.")
    build_meteorological_spatial_support(config_path)
    support = load_meteorological_support(weather.h3_resolution, config_path)
    grid_hash = cell_set_hash(support["H3_INDEX"])
    source_grid = pd.DataFrame({
        "SOURCE_GRID_INDEX": range(len(support)),
        "SOURCE_LAT": support["CENTROID_LAT"].to_numpy(dtype=float),
        "SOURCE_LON": support["CENTROID_LON"].to_numpy(dtype=float),
        "SOURCE_GRID_HASH": grid_hash,
    })
    crosswalk = build_nearest_grid_crosswalk(support, source_grid)
    crosswalk_path = weather.raw_dir / "crosswalks" / f"{grid_hash}.parquet"
    write_table(crosswalk_path, pa.Table.from_pandas(crosswalk, preserve_index=False), HRRR_CROSSWALK_SCHEMA)
    rows = []
    for index, valid in enumerate(make_sample_times_for_local_date(date, weather.timezone, weather.interval_hours)):
        valid = pd.Timestamp(valid).tz_convert("UTC")
        raw = source_grid.assign(
            VALID_TIME_UTC=valid.isoformat(), INIT_TIME_UTC=valid.isoformat(),
            AVAILABLE_AT_UTC=(valid + pd.Timedelta(hours=6)).isoformat(),
            SOURCE_MODEL="synthetic_hrrr", SOURCE_PRODUCT="sfc", FORECAST_HOUR=0,
            SOURCE_WIND_BASIS="earth_relative",
            TEMPERATURE_2M_K=280.15 + index,
            RELATIVE_HUMIDITY_2M_PCT=65.0, U_WIND_10M_MS=2.0,
            V_WIND_10M_MS=-3.0, WIND_GUST_SURFACE_MS=8.0,
            VISIBILITY_M=20_000.0, TOTAL_CLOUD_COVER_PCT=40.0,
            PRECIP_RATE_KG_M2_S=0.00001 * (index + 1),
            MEAN_SEA_LEVEL_PRESSURE_PA=101_000.0,
        )
        sample = sample_source_grid(
            raw, crosswalk, local_date=date, valid_time_utc=valid,
            precip_init_time_utc=valid - pd.Timedelta(hours=1), precip_forecast_hour=1,
        )
        destination = sample_path(weather.raw_dir, valid, weather.timezone)
        write_table(destination, pa.Table.from_pandas(sample, preserve_index=False), HRRR_SAMPLE_SCHEMA)
        row = _inventory_row(
            valid_time=valid, timezone=weather.timezone, availability_lag_hours=6,
            h3_resolution=5, h3_cell_count=len(support), raw_dir=weather.raw_dir,
            sample=destination, crosswalk=crosswalk_path, status="COMPLETE",
            source_uri=f"synthetic://fixture/f00/{valid.isoformat()}",
            precip_source_uri=f"synthetic://fixture/f01/{valid.isoformat()}",
            source_grid_hash=grid_hash,
        )
        row.update(SOURCE_BACKEND="synthetic_fixture", SOURCE_FORMAT="SYNTHETIC_PARQUET",
                   PRECISION_QC_STATE="DETERMINISTIC_SYNTHETIC",
                   SOURCE_OBJECT_URI=None, PRECIP_OBJECT_URI=None)
        rows.append(row)
    write_table(weather.inventory_path, pa.Table.from_pylist(rows, schema=HRRR_INVENTORY_SCHEMA), HRRR_INVENTORY_SCHEMA)
    acquisition = manifest_payload(
        product="meteorological.surface_weather.download", run_id="synthetic-offline-acquisition",
        config_path=config.path,
        resolved_config={"start_date": date, "end_date": date, "timezone": weather.timezone,
                         "interval_hours": 4, "h3_resolution": 5,
                         "availability_lag_hours": 6,
                         "source": {"model": "synthetic_hrrr", "product": "sfc", "forecast_hour": 0,
                                    "precipitation_forecast_hour": 1}},
        artifacts=[parquet_contract(weather.inventory_path)],
        inputs=[{"path": str(config.support_path(5)), "checksum": checksum_path(config.support_path(5))},
                {"path": str(config.support_manifest_path), "checksum": checksum_path(config.support_manifest_path)}],
        sources=[{"name": "Deterministic synthetic HRRR-like fixture", "license": "Apache-2.0",
                  "attribution": "toolkit-meteorology offline example", "observation_period": "Not observed",
                  "redistribution_restrictions": "None", "synthetic": True}],
        h3_resolution=5, spatial_bounds=config.bbox,
        temporal_coverage={"start_date": date, "end_date": date},
        availability_semantics={"product_type": "synthetic_offline_fixture"},
        limitations=["Synthetic data only; no NOAA acquisition or live provider validation."],
    )
    write_manifest(weather.acquisition_manifest_path, acquisition)
    weather_outputs = build_surface_weather(config_path)
    daylight_outputs = build_daylight(config_path)
    lunar_outputs = build_lunar(config_path)
    matrix = project_root() / "outputs" / "synthetic-daily-matrix.parquet"
    export([weather_outputs[-1], daylight_outputs[-1], lunar_outputs[-1]], matrix)
    return {
        "synthetic": True,
        "acquisition": validate_product(weather.acquisition_manifest_path),
        "weather": validate_product(weather_outputs[-1]),
        "daylight": validate_product(daylight_outputs[-1]),
        "lunar": validate_product(lunar_outputs[-1]),
        "daily_matrix": validate_daily_matrix(matrix),
        "output": str(matrix),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/data/environment_meteorological.yaml"))
    args = parser.parse_args()
    import json

    print(json.dumps(build_offline_example(args.config), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
