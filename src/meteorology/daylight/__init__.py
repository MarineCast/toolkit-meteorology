"""Deterministic daily daylight features for observer-effort modeling."""

from __future__ import annotations

from .compute import (
    compute_daylight_day_of_year_table,
    compute_daylight_hours,
    compute_daylight_table,
    solar_day_365,
)
from .features import (
    build_daylight_day_of_year_features,
    build_daylight_features,
    compact_daylight_weight_output,
    normalize_daylight_features,
)
from .validation import summarize_daylight_features, validate_daylight_features

__all__ = [
    "build_daylight_features",
    "build_daylight_day_of_year_features",
    "compact_daylight_weight_output",
    "compute_daylight_hours",
    "compute_daylight_day_of_year_table",
    "compute_daylight_table",
    "normalize_daylight_features",
    "solar_day_365",
    "summarize_daylight_features",
    "validate_daylight_features",
]
