from __future__ import annotations

import math

import pandas as pd
import pyarrow as pa
import pytest

from meteorology.field_contracts import display_range, regional_warning_counts, validate_fields


def test_pressure_hard_boundary_and_regional_warning_are_distinct() -> None:
    schema = pa.schema([pa.field("MEAN_SEA_LEVEL_PRESSURE_HPA", pa.float64(), nullable=False)])
    accepted = pd.DataFrame({"MEAN_SEA_LEVEL_PRESSURE_HPA": [700.0, 750.0, 1100.0, 1200.0]})
    validate_fields(accepted, schema, context="pressure")
    assert regional_warning_counts(accepted) == {"MEAN_SEA_LEVEL_PRESSURE_HPA": 3}
    assert display_range("MEAN_SEA_LEVEL_PRESSURE_HPA") == "700..1200"
    for value in (699.9, 1200.1, math.inf, math.nan):
        with pytest.raises(ValueError):
            validate_fields(pd.DataFrame({"MEAN_SEA_LEVEL_PRESSURE_HPA": [value]}), schema, context="pressure")


def test_nullable_calm_direction_and_strict_angle_endpoint() -> None:
    schema = pa.schema([pa.field("WIND_DIRECTION_FROM_10M_DEG", pa.float64(), nullable=True)])
    validate_fields(pd.DataFrame({"WIND_DIRECTION_FROM_10M_DEG": [0.0, 359.999, math.nan]}), schema, context="wind")
    with pytest.raises(ValueError, match="outside its declared range"):
        validate_fields(pd.DataFrame({"WIND_DIRECTION_FROM_10M_DEG": [360.0]}), schema, context="wind")
