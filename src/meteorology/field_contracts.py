"""Shared hard field limits and regional diagnostics for current products.

Arrow schemas own field types and ordinary nullability. Conditional null rules
(calm wind and no-night/no-daylight astronomy) remain in their paired validators.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyarrow as pa

FIELD_CONTRACT_VERSION = "meteorology-field-contract-v1"


@dataclass(frozen=True)
class FieldLimit:
    minimum: float
    maximum: float = math.inf
    maximum_exclusive: bool = False

    def label(self) -> str:
        upper = "inf" if math.isinf(self.maximum) else f"{self.maximum:g}"
        return f"{self.minimum:g}..{'<' if self.maximum_exclusive else ''}{upper}"


HARD_LIMITS: dict[str, FieldLimit] = {
    "TEMPERATURE_2M_C": FieldLimit(-100, 70),
    "RELATIVE_HUMIDITY_2M_PCT": FieldLimit(0, 100),
    "WIND_SPEED_10M_MS": FieldLimit(0),
    "WIND_GUST_SURFACE_MS": FieldLimit(0),
    "VISIBILITY_KM": FieldLimit(0),
    "TOTAL_CLOUD_COVER_PCT": FieldLimit(0, 100),
    "PRECIP_RATE_MM_HR": FieldLimit(0),
    "MEAN_SEA_LEVEL_PRESSURE_HPA": FieldLimit(700, 1200),
    "TEMPERATURE_2M_C_MEAN": FieldLimit(-100, 70),
    "RELATIVE_HUMIDITY_2M_PCT_MEAN": FieldLimit(0, 100),
    "TOTAL_CLOUD_COVER_PCT_MEAN": FieldLimit(0, 100),
    "WIND_DIRECTION_FROM_10M_DEG": FieldLimit(0, 360, maximum_exclusive=True),
    "WIND_SPEED_10M_MS_MEAN": FieldLimit(0),
    "WIND_SPEED_10M_MS_MAX": FieldLimit(0),
    "WIND_VECTOR_SPEED_10M_MS": FieldLimit(0),
    "WIND_GUST_SURFACE_MS_MEAN": FieldLimit(0),
    "WIND_GUST_SURFACE_MS_MAX": FieldLimit(0),
    "VISIBILITY_KM_MEAN": FieldLimit(0),
    "VISIBILITY_KM_MIN": FieldLimit(0),
    "SOURCE_GRID_DISTANCE_M_MEAN": FieldLimit(0),
    "SOURCE_GRID_DISTANCE_M_MAX": FieldLimit(0),
    "PRECIP_MM_DAY_ESTIMATE": FieldLimit(0),
    "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN": FieldLimit(700, 1200),
    "MEAN_SEA_LEVEL_PRESSURE_HPA_MIN": FieldLimit(700, 1200),
    "DAYLIGHT_HOURS": FieldLimit(0, 24),
    "DAYLIGHT_FRACTION": FieldLimit(0, 1),
    "SOLAR_ELEVATION_MAX_DEG": FieldLimit(-90, 90),
    "SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG": FieldLimit(0, 90),
    "LOW_SUN_DAYLIGHT_HOURS": FieldLimit(0),
    "LUNAR_PHASE_ANGLE_DEG": FieldLimit(0, 360),
    "LUNAR_ILLUMINATION_FRACTION": FieldLimit(0, 1),
    "NIGHT_HOURS": FieldLimit(0),
    "MOON_VISIBLE_HOURS": FieldLimit(0),
    "MOON_VISIBLE_DARK_HOURS": FieldLimit(0),
    "MOONLIT_DARK_HOURS": FieldLimit(0),
    "MOON_VISIBLE_DARK_FRACTION": FieldLimit(0, 1),
    "MOONLIT_DARK_FRACTION": FieldLimit(0, 1),
}

# These are diagnostic values for the current region, never publication gates.
REGIONAL_WARNINGS: dict[str, FieldLimit] = {
    name: FieldLimit(800, 1100)
    for name in (
        "MEAN_SEA_LEVEL_PRESSURE_HPA",
        "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN",
        "MEAN_SEA_LEVEL_PRESSURE_HPA_MIN",
    )
}

INTEGRATED_HOUR_FIELDS = frozenset({
    "LOW_SUN_DAYLIGHT_HOURS", "NIGHT_HOURS", "MOON_VISIBLE_HOURS",
    "MOON_VISIBLE_DARK_HOURS", "MOONLIT_DARK_HOURS",
})


def display_range(name: str) -> str | None:
    if name in INTEGRATED_HOUR_FIELDS:
        return "0..local civil-day hours (23/24/25)"
    limit = HARD_LIMITS.get(name)
    return limit.label() if limit is not None else None


def validate_fields(frame: pd.DataFrame, schema: pa.Schema, *, context: str) -> None:
    """Apply one null, finite and hard-range policy to producer and reader rows."""

    required = [field.name for field in schema if not field.nullable]
    if frame[required].isna().any().any():
        raise ValueError(f"{context}: non-nullable fields contain null values.")
    numeric = frame.select_dtypes(include="number")
    if np.isinf(numeric.to_numpy(dtype=float)).any():
        raise ValueError(f"{context}: non-finite numeric value.")
    for column, limit in HARD_LIMITS.items():
        if column not in frame:
            continue
        values = frame[column].dropna().to_numpy(dtype=float)
        too_high = values >= limit.maximum if limit.maximum_exclusive else values > limit.maximum
        if (values < limit.minimum).any() or too_high.any():
            raise ValueError(f"{context}: {column} is outside its declared range.")


def regional_warning_counts(frame: pd.DataFrame) -> dict[str, int]:
    """Count regional outliers without rejecting physically allowed records."""

    result = {}
    for column, limit in REGIONAL_WARNINGS.items():
        if column in frame:
            values = frame[column].dropna().to_numpy(dtype=float)
            result[column] = int(((values < limit.minimum) | (values > limit.maximum)).sum())
    return result
