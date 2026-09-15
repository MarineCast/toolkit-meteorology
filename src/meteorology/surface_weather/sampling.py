"""Time and nearest-grid sampling for strict R5 HRRR acquisition."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from meteorology.core.data.meteorological_schemas import HRRR_CROSSWALK_SCHEMA as CROSSWALK_SCHEMA
from meteorology.core.data.meteorological_schemas import HRRR_SAMPLE_SCHEMA as SAMPLE_SCHEMA
from meteorology.core.data.meteorological_schemas import (
    PRE_F01_SAMPLE_SCHEMA,
)

EARTH_RADIUS_M = 6_371_008.8


def make_local_dates(start_date: str, end_date: str) -> list[str]:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if start > end:
        raise ValueError("start_date must be before or equal to end_date.")
    return [ts.strftime("%Y-%m-%d") for ts in pd.date_range(start, end, freq="D")]


def make_sample_times_for_local_date(
    date: str,
    timezone: str,
    interval_hours: int,
) -> list[pd.Timestamp]:
    if interval_hours <= 0 or 24 % int(interval_hours) != 0:
        raise ValueError("interval_hours must be positive and divide 24 exactly.")
    naive = pd.date_range(
        pd.Timestamp(date).normalize(),
        periods=24 // int(interval_hours),
        freq=f"{int(interval_hours)}h",
    )
    localized = naive.tz_localize(timezone, nonexistent="shift_forward", ambiguous="infer")
    return [pd.Timestamp(ts).tz_convert("UTC") for ts in localized]


def build_nearest_grid_crosswalk(
    support: pd.DataFrame,
    source_grid: pd.DataFrame,
) -> pd.DataFrame:
    """Map every support cell to the closest row in one identified HRRR grid."""

    required_support = {"H3_INDEX", "CENTROID_LAT", "CENTROID_LON"}
    required_grid = {"SOURCE_GRID_INDEX", "SOURCE_LAT", "SOURCE_LON", "SOURCE_GRID_HASH"}
    if missing := sorted(required_support.difference(support.columns)):
        raise ValueError(f"Meteorological support is missing columns: {missing}")
    if missing := sorted(required_grid.difference(source_grid.columns)):
        raise ValueError(f"HRRR source grid is missing crosswalk columns: {missing}")
    hashes = set(source_grid["SOURCE_GRID_HASH"].astype(str))
    if len(hashes) != 1:
        raise ValueError("HRRR source grid must contain exactly one grid hash.")
    coordinates = source_grid[["SOURCE_LAT", "SOURCE_LON"]].to_numpy(dtype=float)
    if not np.isfinite(coordinates).all():
        raise ValueError("HRRR source-grid coordinates contain non-finite values.")
    tree = BallTree(np.deg2rad(coordinates), metric="haversine")
    distance, index = tree.query(
        np.deg2rad(support[["CENTROID_LAT", "CENTROID_LON"]].to_numpy(dtype=float)), k=1
    )
    source_rows = source_grid.iloc[index[:, 0]]["SOURCE_GRID_INDEX"].to_numpy(dtype="int32")
    frame = pd.DataFrame(
        {
            "H3_INDEX": support["H3_INDEX"].astype(str).to_numpy(),
            "SOURCE_GRID_HASH": next(iter(hashes)),
            "SOURCE_GRID_INDEX": source_rows,
            "SOURCE_GRID_DISTANCE_M": distance[:, 0] * EARTH_RADIUS_M,
        }
    ).sort_values("H3_INDEX", ignore_index=True)
    if (
        frame["H3_INDEX"].duplicated().any()
        or not np.isfinite(frame["SOURCE_GRID_DISTANCE_M"].to_numpy(dtype=float)).all()
    ):
        raise ValueError("Generated HRRR crosswalk is invalid.")
    return frame[CROSSWALK_SCHEMA.names]


def sample_source_grid(
    source_grid: pd.DataFrame,
    crosswalk: pd.DataFrame,
    *,
    local_date: str,
    valid_time_utc: pd.Timestamp,
    precip_init_time_utc: pd.Timestamp,
    precip_forecast_hour: int,
) -> pd.DataFrame:
    """Normalize one strict source grid to the compact R5 timestamp contract."""

    grid_hashes = set(source_grid["SOURCE_GRID_HASH"].astype(str))
    crosswalk_hashes = set(crosswalk["SOURCE_GRID_HASH"].astype(str))
    if len(grid_hashes) != 1 or grid_hashes != crosswalk_hashes:
        raise ValueError("HRRR source grid and crosswalk hashes do not match.")
    if crosswalk["H3_INDEX"].duplicated().any():
        raise ValueError("HRRR crosswalk contains duplicate H3 cells.")
    indexed = source_grid.set_index("SOURCE_GRID_INDEX", drop=False)
    requested = crosswalk["SOURCE_GRID_INDEX"].to_numpy(dtype="int32")
    missing_rows = sorted(set(requested).difference(indexed.index))
    if missing_rows:
        raise ValueError(f"HRRR crosswalk references an absent source row: {missing_rows[0]}")
    sampled = indexed.loc[requested].reset_index(drop=True)
    valid = pd.Timestamp(valid_time_utc)
    valid = valid.tz_localize("UTC") if valid.tzinfo is None else valid.tz_convert("UTC")
    out = pd.DataFrame(
        {
            "H3_INDEX": crosswalk["H3_INDEX"].astype(str).to_numpy(),
            "DATE": str(local_date),
            "VALID_TIME_UTC": valid.isoformat(),
            "INIT_TIME_UTC": sampled["INIT_TIME_UTC"].astype(str).to_numpy(),
            "AVAILABLE_AT_UTC": sampled["AVAILABLE_AT_UTC"].astype(str).to_numpy(),
            "TEMPERATURE_2M_C": sampled["TEMPERATURE_2M_K"].to_numpy(dtype=float) - 273.15,
            "RELATIVE_HUMIDITY_2M_PCT": sampled["RELATIVE_HUMIDITY_2M_PCT"].to_numpy(dtype=float),
            "U_WIND_10M_MS": sampled["U_WIND_10M_MS"].to_numpy(dtype=float),
            "V_WIND_10M_MS": sampled["V_WIND_10M_MS"].to_numpy(dtype=float),
            "WIND_GUST_SURFACE_MS": sampled["WIND_GUST_SURFACE_MS"].to_numpy(dtype=float),
            "VISIBILITY_KM": sampled["VISIBILITY_M"].to_numpy(dtype=float) / 1000.0,
            "TOTAL_CLOUD_COVER_PCT": sampled["TOTAL_CLOUD_COVER_PCT"].to_numpy(dtype=float),
            "PRECIP_RATE_MM_HR": sampled["PRECIP_RATE_KG_M2_S"].to_numpy(dtype=float) * 3600.0,
            "MEAN_SEA_LEVEL_PRESSURE_HPA": sampled["MEAN_SEA_LEVEL_PRESSURE_PA"].to_numpy(
                dtype=float
            )
            / 100.0,
            "SOURCE_GRID_HASH": sampled["SOURCE_GRID_HASH"].astype(str).to_numpy(),
            "SOURCE_GRID_INDEX": requested,
            "SOURCE_GRID_DISTANCE_M": crosswalk["SOURCE_GRID_DISTANCE_M"].to_numpy(dtype=float),
            "SOURCE_MODEL": sampled["SOURCE_MODEL"].astype(str).to_numpy(),
            "SOURCE_PRODUCT": sampled["SOURCE_PRODUCT"].astype(str).to_numpy(),
            "FORECAST_HOUR": sampled["FORECAST_HOUR"].to_numpy(dtype="int16"),
            "SOURCE_DATA_STATE": "COMPLETE",
            "PRECIP_INIT_TIME_UTC": pd.Timestamp(precip_init_time_utc)
            .tz_convert("UTC")
            .isoformat(),
            "PRECIP_VALID_TIME_UTC": valid.isoformat(),
            "PRECIP_FORECAST_HOUR": int(precip_forecast_hour),
            "PRECIP_SOURCE_PRODUCT": "sfc",
        }
    )
    out["WIND_SPEED_10M_MS"] = np.hypot(out["U_WIND_10M_MS"], out["V_WIND_10M_MS"])
    out = out[SAMPLE_SCHEMA.names].sort_values("H3_INDEX", ignore_index=True)
    numeric = out.select_dtypes(include=[np.number]).to_numpy(dtype=float)
    if len(out) != len(crosswalk) or out["H3_INDEX"].duplicated().any():
        raise ValueError("Sampled HRRR timestamp does not cover the configured H3 support.")
    if not np.isfinite(numeric).all() or out.isna().any().any():
        raise ValueError("Sampled HRRR timestamp contains missing or non-finite values.")
    return out


def replace_sample_precipitation(
    sample: pd.DataFrame,
    precip_grid: pd.DataFrame,
    crosswalk: pd.DataFrame,
    *,
    valid_time_utc: pd.Timestamp,
    forecast_hour: int,
) -> pd.DataFrame:
    """Repair one cached R5 sample with forecast PRATE while retaining core f00 fields."""

    required_grid = {
        "SOURCE_GRID_INDEX",
        "SOURCE_GRID_HASH",
        "PRECIP_RATE_KG_M2_S",
        "PRECIP_INIT_TIME_UTC",
        "PRECIP_VALID_TIME_UTC",
        "PRECIP_FORECAST_HOUR",
    }
    if missing := sorted(required_grid.difference(precip_grid.columns)):
        raise ValueError(f"Forecast precipitation grid is missing columns: {missing}")
    if not set(PRE_F01_SAMPLE_SCHEMA.names).issubset(sample.columns):
        raise ValueError("Cached sample does not contain the reusable f00 core contract.")
    grid_hashes = set(precip_grid["SOURCE_GRID_HASH"].astype(str))
    crosswalk_hashes = set(crosswalk["SOURCE_GRID_HASH"].astype(str))
    sample_hashes = set(sample["SOURCE_GRID_HASH"].astype(str))
    if len(grid_hashes) != 1 or grid_hashes != crosswalk_hashes or grid_hashes != sample_hashes:
        raise ValueError("Forecast precipitation grid does not match the cached HRRR grid.")
    indexed = precip_grid.set_index("SOURCE_GRID_INDEX", drop=False)
    requested = crosswalk["SOURCE_GRID_INDEX"].to_numpy(dtype="int32")
    if missing_rows := sorted(set(requested).difference(indexed.index)):
        raise ValueError(
            f"Forecast precipitation crosswalk references an absent row: {missing_rows[0]}"
        )
    sampled = indexed.loc[requested].reset_index(drop=True)
    repaired = (
        sample[list(PRE_F01_SAMPLE_SCHEMA.names)]
        .assign(H3_INDEX=sample["H3_INDEX"].astype(str))
        .set_index("H3_INDEX")
        .reindex(crosswalk["H3_INDEX"].astype(str))
        .reset_index()
    )
    valid = pd.Timestamp(valid_time_utc)
    valid = valid.tz_localize("UTC") if valid.tzinfo is None else valid.tz_convert("UTC")
    init = valid - pd.Timedelta(hours=int(forecast_hour))
    if set(sampled["PRECIP_VALID_TIME_UTC"].astype(str)) != {valid.isoformat()}:
        raise ValueError("Forecast precipitation valid time does not match the target sample.")
    if set(sampled["PRECIP_INIT_TIME_UTC"].astype(str)) != {init.isoformat()}:
        raise ValueError("Forecast precipitation initialization time is incorrect.")
    if set(sampled["PRECIP_FORECAST_HOUR"].astype(int)) != {int(forecast_hour)}:
        raise ValueError("Forecast precipitation hour is incorrect.")
    repaired["PRECIP_RATE_MM_HR"] = sampled["PRECIP_RATE_KG_M2_S"].to_numpy(dtype=float) * 3600.0
    repaired["PRECIP_INIT_TIME_UTC"] = init.isoformat()
    repaired["PRECIP_VALID_TIME_UTC"] = valid.isoformat()
    repaired["PRECIP_FORECAST_HOUR"] = int(forecast_hour)
    repaired["PRECIP_SOURCE_PRODUCT"] = "sfc"
    repaired = repaired[SAMPLE_SCHEMA.names].sort_values("H3_INDEX", ignore_index=True)
    numeric = repaired.select_dtypes(include=[np.number]).to_numpy(dtype=float)
    if repaired.isna().any().any() or not np.isfinite(numeric).all():
        raise ValueError("Repaired HRRR sample contains missing or non-finite values.")
    return repaired
