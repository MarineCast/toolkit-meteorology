"""Feature normalization helpers for deterministic daylight panels.

Observer-effort schema
----------------------
``daylight_fraction`` is the primary absolute daylight-availability proxy and
the default copied into ``daylight_weight``. ``daylight_weight_cell_norm`` is a
within-cell seasonal diagnostic normalized against a fixed full annual solar
cycle. ``daylight_weight_global_norm`` is a cross-cell annual diagnostic using
the same fixed basis.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from .compute import (
    compute_daylight_day_of_year_table,
    compute_daylight_table,
)
from .validation import validate_daylight_features

DefaultWeight = Literal["cell_norm", "global_norm", "fraction"]
NORMALIZATION_BASIS = (
    "Fixed full annual 365-day solar cycle per H3 centroid. Feb 29 maps to "
    "solar_day_365=60 and uses the same solar-day proxy as Mar 1."
)


def _clip_series(values: pd.Series, quantile_clip: float | tuple[float, float] | None) -> pd.Series:
    if quantile_clip is None:
        return values
    if isinstance(quantile_clip, tuple):
        lower_q, upper_q = quantile_clip
    else:
        lower_q = float(quantile_clip)
        upper_q = 1.0 - float(quantile_clip)
    if not 0.0 <= float(lower_q) <= float(upper_q) <= 1.0:
        raise ValueError("quantile_clip must define quantiles in ascending [0, 1] order.")
    return values.clip(values.quantile(lower_q), values.quantile(upper_q))


def _annual_daylight_for_cells(cells: pd.DataFrame) -> pd.DataFrame:
    days = pd.DataFrame({"solar_day_365": np.arange(1, 366, dtype=np.int64)})
    cells = cells[["h3", "centroid_lat"]].copy()
    cells["_join_key"] = 1
    days["_join_key"] = 1
    annual = cells.merge(days, on="_join_key", how="inner").drop(columns="_join_key")
    annual["daylight_hours"] = _daylight_for_latitudes(
        annual["centroid_lat"].to_numpy(dtype=float),
        annual["solar_day_365"].to_numpy(dtype=int),
    )
    return annual


def _normalization_bounds(
    cells: pd.DataFrame,
    quantile_clip: float | tuple[float, float] | None = None,
) -> dict[str, object]:
    annual = _annual_daylight_for_cells(cells)
    annual["bounded_daylight_hours"] = _clip_series(annual["daylight_hours"], quantile_clip)
    grouped = annual.groupby(annual["h3"].astype("string"), sort=False)["bounded_daylight_hours"]
    cell_min = grouped.min()
    cell_max = grouped.max()
    global_values = annual["bounded_daylight_hours"]
    return {
        "cell_min": cell_min,
        "cell_max": cell_max,
        "global_min": float(global_values.min()) if len(global_values) else 0.0,
        "global_max": float(global_values.max()) if len(global_values) else 0.0,
        "basis": NORMALIZATION_BASIS,
    }


def _apply_normalized_weights(
    daylight_df: pd.DataFrame,
    bounds: dict[str, object],
    default_weight: DefaultWeight,
) -> pd.DataFrame:
    if default_weight not in {"cell_norm", "global_norm", "fraction"}:
        raise ValueError("default_weight must be one of: cell_norm, global_norm, fraction.")

    out = daylight_df.copy()
    hours = pd.to_numeric(out["daylight_hours"], errors="coerce")
    if "daylight_fraction" not in out.columns:
        out["daylight_fraction"] = hours / 24.0
    h3 = out["h3"].astype("string")
    cell_min = h3.map(bounds["cell_min"])  # type: ignore[arg-type]
    cell_max = h3.map(bounds["cell_max"])  # type: ignore[arg-type]
    cell_denom = (cell_max - cell_min).where((cell_max - cell_min) != 0)
    out["daylight_weight_cell_norm"] = ((hours - cell_min) / cell_denom).fillna(1.0).clip(0.0, 1.0)

    global_min = float(bounds["global_min"])
    global_max = float(bounds["global_max"])
    global_denom = global_max - global_min
    if global_denom == 0:
        out["daylight_weight_global_norm"] = 1.0
    else:
        out["daylight_weight_global_norm"] = ((hours - global_min) / global_denom).clip(0.0, 1.0)

    fraction = pd.to_numeric(out["daylight_fraction"], errors="coerce").clip(0.0, 1.0)
    if default_weight == "cell_norm":
        out["daylight_weight"] = out["daylight_weight_cell_norm"]
    elif default_weight == "global_norm":
        out["daylight_weight"] = out["daylight_weight_global_norm"]
    else:
        out["daylight_weight"] = fraction
    return out


def normalize_daylight_features(
    daylight_df: pd.DataFrame,
    default_weight: DefaultWeight = "fraction",
    quantile_clip: float | tuple[float, float] | None = None,
) -> pd.DataFrame:
    """Add daylight effort weights using fixed annual normalization bounds."""
    if "daylight_hours" not in daylight_df.columns:
        raise ValueError("daylight_df is missing required column: daylight_hours")
    if "h3" not in daylight_df.columns:
        raise ValueError("daylight_df is missing required column: h3")
    if "centroid_lat" not in daylight_df.columns:
        raise ValueError("daylight_df is missing required column: centroid_lat")

    out = daylight_df.copy()
    cells = out[["h3", "centroid_lat"]].drop_duplicates(subset=["h3"], keep="first")
    bounds = _normalization_bounds(cells, quantile_clip=quantile_clip)
    return _apply_normalized_weights(out, bounds, default_weight)


def build_daylight_features(
    h3_df: pd.DataFrame,
    start_date: str,
    end_date: str,
    h3_column: str = "h3",
    latitude_column: str = "centroid_lat",
    longitude_column: str = "centroid_lon",
    default_weight: DefaultWeight = "fraction",
    quantile_clip: float | tuple[float, float] | None = None,
    validate: bool = True,
) -> pd.DataFrame:
    """Compute and normalize daylight features from an in-memory H3 centroid table."""
    daylight = compute_daylight_table(
        h3_df,
        start_date=start_date,
        end_date=end_date,
        h3_column=h3_column,
        latitude_column=latitude_column,
        longitude_column=longitude_column,
    )
    features = normalize_daylight_features(
        daylight,
        default_weight=default_weight,
        quantile_clip=quantile_clip,
    )
    if validate:
        validate_daylight_features(features, strict=True)
    return features


def build_daylight_day_of_year_features(
    h3_df: pd.DataFrame,
    h3_column: str = "h3",
    latitude_column: str = "centroid_lat",
    longitude_column: str = "centroid_lon",
    default_weight: DefaultWeight = "fraction",
    max_day_of_year: int = 366,
    validate: bool = True,
) -> pd.DataFrame:
    """Compute and normalize compact H3 x day-of-year daylight features."""
    daylight = compute_daylight_day_of_year_table(
        h3_df,
        h3_column=h3_column,
        latitude_column=latitude_column,
        longitude_column=longitude_column,
        max_day_of_year=max_day_of_year,
    )
    features = normalize_daylight_features(daylight, default_weight=default_weight)
    if validate:
        validate_daylight_features(features, strict=True, require_date=False)
    return features


def compact_daylight_weight_output(features: pd.DataFrame) -> pd.DataFrame:
    """Return final daylight weight columns for model joins."""
    required = {"h3", "day_of_year", "daylight_weight"}
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"Daylight features are missing required columns: {missing}")

    weights = pd.to_numeric(features["daylight_weight"], errors="coerce")
    max_weight = weights.max()
    if pd.isna(max_weight) or float(max_weight) <= 0.0:
        relative = pd.Series(0.0, index=features.index)
    else:
        relative = weights / float(max_weight)

    return pd.DataFrame(
        {
            "h3": features["h3"].astype("string"),
            "day_of_year": pd.to_numeric(features["day_of_year"], errors="raise").astype("int64"),
            "weight_daylight": relative.clip(0.0, 1.0).astype("float32"),
        }
    )


def _daylight_for_latitudes(latitudes: np.ndarray, day_of_year: int | np.ndarray) -> np.ndarray:
    lat_rad = np.deg2rad(np.clip(latitudes.astype(float), -90.0, 90.0))
    day = np.asarray(day_of_year, dtype=float)
    if day.ndim == 0:
        day = np.full(len(latitudes), float(day), dtype=float)
    declination = np.deg2rad(23.44 * np.sin((2.0 * np.pi / 365.0) * (day - 81.0)))
    hour_angle_arg = -np.tan(lat_rad) * np.tan(declination)
    daylight = np.empty(len(latitudes), dtype=float)
    daylight[hour_angle_arg >= 1.0] = 0.0
    daylight[hour_angle_arg <= -1.0] = 24.0
    mask = (hour_angle_arg > -1.0) & (hour_angle_arg < 1.0)
    daylight[mask] = (24.0 / np.pi) * np.arccos(hour_angle_arg[mask])
    return np.clip(daylight, 0.0, 24.0)
