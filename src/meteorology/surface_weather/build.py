"""Build the strict daily R5 surface-weather product from compact HRRR samples."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from meteorology.core.artifacts import TransactionalFamilyPublisher, atomic_write_json
from meteorology.core.data.meteorological_schemas import SURFACE_WEATHER_DAILY_SCHEMA as DAILY_SCHEMA

from ..artifacts import (
    checksum_path,
    load_manifest,
    manifest_payload,
    parquet_contract,
    write_manifest,
    write_table,
)
from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from ..spatial_support.build import load_meteorological_support
from .download import INVENTORY_SCHEMA
from .sampling import AVAILABILITY_POLICY, CROSSWALK_SCHEMA, SAMPLE_SCHEMA, make_sample_times_for_local_date


def _snapshot_replaced_manifest(manifest_path: Path) -> Path | None:
    if not manifest_path.exists():
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(payload.get("h3_resolution", -1)) == 5:
        return None
    destination = manifest_path.parent / "PRE_R5_SURFACE_WEATHER_MANIFEST.json"
    if not destination.exists():
        atomic_write_json(destination, payload, overwrite=False)
    return destination


def _load_inventory(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Canonical R5 HRRR inventory is missing: {path}. Run surface_weather.download first."
        )
    parquet = pq.ParquetFile(path)
    if not parquet.schema_arrow.equals(INVENTORY_SCHEMA, check_metadata=False):
        raise ValueError(f"Canonical R5 HRRR inventory schema is invalid: {path}")
    frame = parquet.read().to_pandas()
    if frame["VALID_TIME_UTC"].duplicated().any():
        raise ValueError("Canonical R5 HRRR inventory has duplicate valid times.")
    return frame


def _read_validated_sample(
    *,
    raw_dir: Path,
    inventory_row: Any,
    expected_time: pd.Timestamp,
    support_cells: set[str],
    source_model: str = "hrrr",
) -> pd.DataFrame:
    if inventory_row is None or str(inventory_row.STATUS) != "COMPLETE":
        raise ValueError(f"HRRR inventory is incomplete for {expected_time.isoformat()}.")
    if not inventory_row.RELATIVE_PATH or not inventory_row.CHECKSUM:
        raise ValueError(f"HRRR inventory lacks sample provenance for {expected_time.isoformat()}.")
    path = raw_dir / str(inventory_row.RELATIVE_PATH)
    if not path.exists() or checksum_path(path) != str(inventory_row.CHECKSUM):
        raise ValueError(f"HRRR sample is missing or has a checksum mismatch: {path}")
    parquet = pq.ParquetFile(path)
    if not parquet.schema_arrow.equals(SAMPLE_SCHEMA, check_metadata=False):
        raise ValueError(f"HRRR sample schema is invalid: {path}")
    frame = parquet.read().to_pandas()
    valid = pd.Timestamp(expected_time).tz_convert("UTC").isoformat()
    if set(frame["VALID_TIME_UTC"].astype(str)) != {valid}:
        raise ValueError(f"HRRR sample valid time is incorrect: {path}")
    if set(frame["AVAILABLE_AT_UTC"].astype(str)) != {str(inventory_row.AVAILABLE_AT_UTC)}:
        raise ValueError(f"HRRR sample availability disagrees with the inventory: {path}")
    if set(frame["AVAILABILITY_POLICY"].astype(str)) != {AVAILABILITY_POLICY}:
        raise ValueError(f"HRRR sample availability policy is incompatible: {path}")
    if (
        set(frame["INIT_TIME_UTC"].astype(str)) != {valid}
        or set(frame["FORECAST_HOUR"].astype(int)) != {0}
        or set(frame["SOURCE_MODEL"].astype(str)) != {source_model}
        or set(frame["SOURCE_PRODUCT"].astype(str)) != {"sfc"}
        or set(frame["DATE"].astype(str)) != {str(inventory_row.LOCAL_DATE)}
    ):
        raise ValueError(f"HRRR sample f00 analysis or local-date identity is incorrect: {path}")
    precip_init = (
        pd.Timestamp(expected_time).tz_convert("UTC") - pd.Timedelta(hours=1)
    ).isoformat()
    if (
        set(frame["PRECIP_VALID_TIME_UTC"].astype(str)) != {valid}
        or set(frame["PRECIP_INIT_TIME_UTC"].astype(str)) != {precip_init}
        or set(frame["PRECIP_FORECAST_HOUR"].astype(int)) != {1}
    ):
        raise ValueError(f"HRRR sample precipitation provenance is incorrect: {path}")
    if len(frame) != len(support_cells) or set(frame["H3_INDEX"].astype(str)) != support_cells:
        raise ValueError(f"HRRR sample support is incomplete: {path}")
    if frame["H3_INDEX"].duplicated().any() or set(frame["SOURCE_DATA_STATE"]) != {"COMPLETE"}:
        raise ValueError(f"HRRR sample keys or data state are invalid: {path}")
    if set(frame["WIND_VECTOR_BASIS"].astype(str)) != {"earth_relative"}:
        raise ValueError(f"HRRR sample wind basis is not earth relative: {path}")
    if not set(frame["SOURCE_WIND_BASIS"].astype(str)).issubset({"grid_relative", "earth_relative"}):
        raise ValueError(f"HRRR sample source wind basis is invalid: {path}")
    numeric = frame.select_dtypes(include=[np.number]).to_numpy(dtype=float)
    if frame.isna().any().any() or not np.isfinite(numeric).all():
        raise ValueError(f"HRRR sample contains missing or non-finite values: {path}")
    bounds = {
        "TEMPERATURE_2M_C": (-100.0, 70.0),
        "RELATIVE_HUMIDITY_2M_PCT": (0.0, 100.0),
        "WIND_SPEED_10M_MS": (0.0, None),
        "WIND_GUST_SURFACE_MS": (0.0, None),
        "VISIBILITY_KM": (0.0, None),
        "TOTAL_CLOUD_COVER_PCT": (0.0, 100.0),
        "PRECIP_RATE_MM_HR": (0.0, None),
        "MEAN_SEA_LEVEL_PRESSURE_HPA": (700.0, 1200.0),
    }
    for column, (minimum, maximum) in bounds.items():
        values = frame[column].to_numpy(dtype=float)
        if (values < minimum).any() or (maximum is not None and (values > maximum).any()):
            raise ValueError(f"HRRR sample {column} is outside its physical validation range: {path}")
    if set(frame["SOURCE_GRID_HASH"].astype(str)) != {str(inventory_row.SOURCE_GRID_HASH)}:
        raise ValueError(f"HRRR sample grid hash disagrees with inventory: {path}")
    return frame


def aggregate_surface_weather_daily(samples: pd.DataFrame, interval_hours: int) -> pd.DataFrame:
    """Aggregate six f00 core samples and matched f01 precipitation rates."""

    if samples.empty:
        return pd.DataFrame(columns=DAILY_SCHEMA.names)
    expected = 24 // int(interval_hours)
    keys = ["H3_INDEX", "DATE"]
    time_columns = ["VALID_TIME_UTC", "AVAILABLE_AT_UTC"]
    if samples[keys + time_columns].isna().any().any():
        raise ValueError("HRRR samples contain missing keys or timestamps.")
    ordered = samples.sort_values([*keys, "VALID_TIME_UTC"], kind="stable")
    counts = ordered.groupby(keys, sort=True, observed=True).size()
    if (
        not counts.eq(expected).all()
        or not ordered["SOURCE_DATA_STATE"].astype(str).eq("COMPLETE").all()
    ):
        raise ValueError("Incomplete HRRR samples for one or more cell/date groups.")
    if ordered.duplicated([*keys, "VALID_TIME_UTC"]).any():
        raise ValueError("Duplicate HRRR valid times within a cell/date group.")

    def values(column: str) -> np.ndarray:
        result = pd.to_numeric(ordered[column], errors="raise").to_numpy(dtype="float64")
        if not np.isfinite(result).all():
            raise ValueError(f"Non-finite {column} in HRRR samples.")
        # Preserve the original per-cell six-value NumPy reduction order.
        # Pandas groupby sums can use different floating-point accumulation.
        return np.ascontiguousarray(result).reshape(len(counts), expected)

    temperature = values("TEMPERATURE_2M_C")
    humidity = values("RELATIVE_HUMIDITY_2M_PCT")
    wind = values("WIND_SPEED_10M_MS")
    u_wind = values("U_WIND_10M_MS")
    v_wind = values("V_WIND_10M_MS")
    gust = values("WIND_GUST_SURFACE_MS")
    visibility = values("VISIBILITY_KM")
    cloud = values("TOTAL_CLOUD_COVER_PCT")
    precip = values("PRECIP_RATE_MM_HR")
    pressure = values("MEAN_SEA_LEVEL_PRESSURE_HPA")
    distance = values("SOURCE_GRID_DISTANCE_M")
    valid_times = ordered["VALID_TIME_UTC"].astype(str).to_numpy(dtype=object).reshape(-1, expected)
    available_times = (
        ordered["AVAILABLE_AT_UTC"].astype(str).to_numpy(dtype=object).reshape(-1, expected)
    )
    group_keys = ordered[keys].drop_duplicates()
    u_mean = u_wind.mean(axis=1)
    v_mean = v_wind.mean(axis=1)
    vector_speed = np.hypot(u_mean, v_mean)
    # Meteorological convention: clockwise from north, direction FROM.
    # A calm mean vector has no direction; never encode it as north (zero).
    direction = np.where(
        vector_speed > 1e-12,
        (np.degrees(np.arctan2(-u_mean, -v_mean)) + 360.0) % 360.0,
        np.nan,
    )
    return pd.DataFrame(
        {
            "H3_INDEX": group_keys["H3_INDEX"].astype(str).to_numpy(),
            "DATE": group_keys["DATE"].astype(str).to_numpy(),
            "TEMPERATURE_2M_C_MEAN": temperature.mean(axis=1),
            "RELATIVE_HUMIDITY_2M_PCT_MEAN": humidity.mean(axis=1),
            "WIND_SPEED_10M_MS_MEAN": wind.mean(axis=1),
            "WIND_SPEED_10M_MS_MAX": wind.max(axis=1),
            "U_WIND_10M_MS_MEAN": u_mean,
            "V_WIND_10M_MS_MEAN": v_mean,
            "WIND_VECTOR_SPEED_10M_MS": vector_speed,
            "WIND_DIRECTION_FROM_10M_DEG": direction,
            "WIND_GUST_SURFACE_MS_MEAN": gust.mean(axis=1),
            "WIND_GUST_SURFACE_MS_MAX": gust.max(axis=1),
            "VISIBILITY_KM_MEAN": visibility.mean(axis=1),
            "VISIBILITY_KM_MIN": visibility.min(axis=1),
            "TOTAL_CLOUD_COVER_PCT_MEAN": cloud.mean(axis=1),
            "PRECIP_MM_DAY_ESTIMATE": (precip * float(interval_hours)).sum(axis=1),
            "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN": pressure.mean(axis=1),
            "MEAN_SEA_LEVEL_PRESSURE_HPA_MIN": pressure.min(axis=1),
            "EXPECTED_SAMPLE_COUNT": expected,
            "SAMPLE_COUNT": expected,
            "SAMPLE_COVERAGE_FRAC": 1.0,
            "FIRST_VALID_TIME_UTC": valid_times[:, 0],
            "LAST_VALID_TIME_UTC": valid_times[:, -1],
            "LATEST_AVAILABLE_AT_UTC": available_times.max(axis=1),
            "SOURCE_GRID_DISTANCE_M_MEAN": distance.mean(axis=1),
            "SOURCE_GRID_DISTANCE_M_MAX": distance.max(axis=1),
            "QC_STATE": "COMPLETE",
        },
        columns=DAILY_SCHEMA.names,
    )


def validate_surface_weather_daily(
    daily: pd.DataFrame,
    support: pd.DataFrame,
    dates: list[str],
    expected_samples: int,
) -> None:
    if daily.duplicated(["H3_INDEX", "DATE"]).any():
        raise ValueError("Daily R5 weather contains duplicate H3/date keys.")
    support_cells = set(support["H3_INDEX"].astype(str))
    if set(daily["DATE"].astype(str)) != set(dates):
        raise ValueError("Daily R5 weather date coverage is incomplete.")
    for date, group in daily.groupby("DATE"):
        if set(group["H3_INDEX"].astype(str)) != support_cells:
            raise ValueError(f"Daily R5 weather support mismatch for {date}.")
    required = daily.drop(columns=["WIND_DIRECTION_FROM_10M_DEG"])
    if required.isna().any().any():
        raise ValueError("Daily R5 weather contains null values.")
    numeric = required.select_dtypes(include=[np.number]).to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise ValueError("Daily R5 weather contains non-finite values.")
    if set(daily["QC_STATE"].astype(str)) != {"COMPLETE"}:
        raise ValueError("Daily R5 weather contains a non-complete QC state.")
    if set(daily["EXPECTED_SAMPLE_COUNT"].astype(int)) != {expected_samples}:
        raise ValueError("Daily R5 weather expected-sample count is incorrect.")
    if set(daily["SAMPLE_COUNT"].astype(int)) != {expected_samples}:
        raise ValueError("Daily R5 weather sample count is incomplete.")
    if set(daily["SAMPLE_COVERAGE_FRAC"].astype(float)) != {1.0}:
        raise ValueError("Daily R5 weather sample coverage is incomplete.")


def build_surface_weather(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    run_id: str | None = None,
    inventory_path: str | Path | None = None,
    acquisition_manifest_path: str | Path | None = None,
) -> tuple[Path, ...]:
    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    inventory_source = Path(inventory_path or weather.inventory_path)
    acquisition_manifest_source = Path(
        acquisition_manifest_path or weather.acquisition_manifest_path
    )
    acquisition_manifest = load_manifest(acquisition_manifest_source, verify_artifacts=True)
    if acquisition_manifest["product"] != "meteorological.surface_weather.download":
        raise ValueError(
            f"Unexpected HRRR acquisition manifest product: {acquisition_manifest['product']}"
        )
    source_model = str(acquisition_manifest.get("resolved_config", {}).get("source", {}).get("model", "hrrr"))
    if source_model not in {"hrrr", "synthetic_hrrr"}:
        raise ValueError(f"Unsupported acquisition source model: {source_model}")
    frozen = acquisition_manifest.get("temporal_coverage", {})
    frozen_start = str(frozen.get("start_date") or "")
    frozen_end = str(frozen.get("end_date") or "")
    start = str(start_date or frozen_start)
    end = str(end_date or frozen_end)
    if (start, end) != (frozen_start, frozen_end):
        raise ValueError(
            "The canonical R5 build must use the complete frozen acquisition range "
            f"{frozen_start} through {frozen_end}."
        )
    dates = [value.strftime("%Y-%m-%d") for value in pd.date_range(start, end, freq="D")]
    if not dates:
        raise ValueError("Surface-weather build date range is empty.")
    inventory = _load_inventory(inventory_source)
    inventory_by_time = {
        pd.Timestamp(row.VALID_TIME_UTC).tz_convert("UTC"): row
        for row in inventory.itertuples(index=False)
    }
    support = load_meteorological_support(weather.h3_resolution, config_path)
    support_cells = set(support["H3_INDEX"].astype(str))
    expected_samples = weather.expected_samples_per_standard_day
    crosswalk_sources: dict[str, tuple[Path, str]] = {}
    run_id = run_id or f"surface-weather-r5-{uuid.uuid4().hex[:12]}"
    crosswalk_destination = weather.daily_output_dir.parent / "H3_HRRR_NEAREST_GRID_RES_5"
    _snapshot_replaced_manifest(weather.manifest_path)
    with TransactionalFamilyPublisher(weather.daily_output_dir.parent, run_id=run_id) as publisher:
        staged_daily = publisher.stage_path(weather.daily_output_dir)
        staged_crosswalk = publisher.stage_path(crosswalk_destination)
        staged_manifest = publisher.stage_manifest_path(weather.manifest_path)
        for local_date in dates:
            timestamp_frames: list[pd.DataFrame] = []
            for valid in make_sample_times_for_local_date(
                local_date, weather.timezone, weather.interval_hours
            ):
                valid = pd.Timestamp(valid).tz_convert("UTC")
                row = inventory_by_time.get(valid)
                sample = _read_validated_sample(
                    raw_dir=weather.raw_dir,
                    inventory_row=row,
                    expected_time=valid,
                    support_cells=support_cells,
                    source_model=source_model,
                )
                timestamp_frames.append(sample)
                if not row.CROSSWALK_RELATIVE_PATH or not row.CROSSWALK_CHECKSUM:
                    raise ValueError(f"HRRR inventory lacks crosswalk provenance for {valid}.")
                crosswalk_sources[str(row.SOURCE_GRID_HASH)] = (
                    weather.raw_dir / str(row.CROSSWALK_RELATIVE_PATH),
                    str(row.CROSSWALK_CHECKSUM),
                )
            daily = aggregate_surface_weather_daily(
                pd.concat(timestamp_frames, ignore_index=True), weather.interval_hours
            )
            validate_surface_weather_daily(daily, support, [local_date], expected_samples)
            write_table(
                staged_daily / f"year={local_date[:4]}" / f"date={local_date}" / "part-000.parquet",
                pa.Table.from_pandas(daily.reset_index(drop=True), preserve_index=False),
                DAILY_SCHEMA,
            )
        for grid_hash, (path, checksum) in sorted(crosswalk_sources.items()):
            if not path.exists() or checksum_path(path) != checksum:
                raise ValueError(f"HRRR crosswalk is missing or has a checksum mismatch: {path}")
            parquet = pq.ParquetFile(path)
            if not parquet.schema_arrow.equals(CROSSWALK_SCHEMA, check_metadata=False):
                raise ValueError(f"HRRR crosswalk schema is invalid: {path}")
            frame = parquet.read().to_pandas()
            if len(frame) != len(support) or set(frame["H3_INDEX"].astype(str)) != support_cells:
                raise ValueError(f"HRRR crosswalk support is incomplete: {path}")
            write_table(
                staged_crosswalk / f"source_grid_hash={grid_hash}" / "part-000.parquet",
                pa.Table.from_pandas(frame, preserve_index=False),
                CROSSWALK_SCHEMA,
            )
        contracts = [
            parquet_contract(staged_daily, published_path=weather.daily_output_dir),
            parquet_contract(staged_crosswalk, published_path=crosswalk_destination),
        ]
        payload = manifest_payload(
            product="meteorological.surface_weather",
            run_id=run_id,
            config_path=config.path,
            resolved_config={
                "start_date": start,
                "end_date": end,
                "timezone": weather.timezone,
                "interval_hours": weather.interval_hours,
                "expected_samples_per_day": expected_samples,
                "h3_resolution": weather.h3_resolution,
                "strict_complete_days_only": True,
            },
            artifacts=contracts,
            inputs=[
                {"path": str(inventory_source), "checksum": checksum_path(inventory_source)},
                {
                    "path": str(acquisition_manifest_source),
                    "checksum": checksum_path(acquisition_manifest_source),
                },
                {
                    "path": str(config.support_path(weather.h3_resolution)),
                    "checksum": checksum_path(config.support_path(weather.h3_resolution)),
                },
                {
                    "path": str(config.support_manifest_path),
                    "checksum": checksum_path(config.support_manifest_path),
                },
            ],
            sources=acquisition_manifest["sources"],
            h3_resolution=weather.h3_resolution,
            spatial_bounds=config.bbox,
            temporal_coverage={"start_date": start, "end_date": end},
            source_completeness="complete",
            availability_semantics={
                "product_type": "synthetic_offline_fixture" if source_model == "synthetic_hrrr" else "retrospective_analysis_with_short_forecast_precipitation",
                "daily_release": "Each local date requires all six configured f00 core analyses and matched f01 precipitation rates for every R5 cell.",
                "sample_availability": "AVAILABLE_AT_UTC is inherited from the direct-HRRR acquisition inventory.",
            },
            formulas={
                "PRECIP_MM_DAY_ESTIMATE": "sum(PRECIP_RATE_MM_HR * 4 hours) across six f01 PRATE forecasts initialized one hour before and valid at the configured sample times",
                "WIND_VECTOR_SPEED_10M_MS": "hypot(mean(U_WIND_10M_MS), mean(V_WIND_10M_MS))",
                "WIND_DIRECTION_FROM_10M_DEG": "mod(degrees(atan2(-mean(U_WIND_10M_MS), -mean(V_WIND_10M_MS))) + 360, 360); null when mean-vector speed <= 1e-12 m/s",
            },
            units={
                "temperature": "degrees Celsius",
                "wind": "metres per second",
                "visibility": "kilometres",
                "precipitation_estimate": "millimetres",
                "pressure": "hectopascals",
            },
            limitations=[
                "PRECIP_MM_DAY_ESTIMATE is a six-snapshot estimate from f01 forecast rates, not an hourly or accumulated 24-hour precipitation analysis.",
                "H3 values are nearest-neighbour samples from the direct HRRR grid.",
                "Partial days and missing or non-finite source values are not published.",
                "An all-zero precipitation range can be valid when every source sample passes identity, completeness, and unit checks.",
                "Wind direction is undefined for a calm mean vector and is null in that case.",
            ],
        )
        write_manifest(staged_manifest, payload)
        publisher.publish()
    return weather.daily_output_dir, crosswalk_destination, weather.manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    for path in build_surface_weather(
        args.config,
        start_date=args.start_date,
        end_date=args.end_date,
        run_id=args.run_id,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
