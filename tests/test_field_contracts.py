from __future__ import annotations

import math

import pandas as pd
import pyarrow as pa
import pytest

from meteorology.field_contracts import display_range, regional_warning_counts, validate_fields
from meteorology.surface_weather.verify import pressure_contract_decision


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


def test_rebuild_pressure_decision_shares_hard_limits_and_keeps_structure_separate() -> None:
    frame = pd.DataFrame({
        "MEAN_SEA_LEVEL_PRESSURE_HPA_MIN": [750.0, 1150.0, 700.0, 1200.0],
        "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN": [750.0, 1150.0, 700.0, 1200.0],
    })
    errors, warnings = pressure_contract_decision(frame, "synthetic")
    assert errors == []
    assert warnings == {
        "MEAN_SEA_LEVEL_PRESSURE_HPA_MIN": 4,
        "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN": 4,
    }
    for value in (699.9, 1200.1, math.inf, math.nan):
        bad = frame.iloc[[0]].copy()
        bad.loc[0, "MEAN_SEA_LEVEL_PRESSURE_HPA_MIN"] = value
        assert pressure_contract_decision(bad, "synthetic")[0]
    inverted = frame.iloc[[0]].copy()
    inverted.loc[0, "MEAN_SEA_LEVEL_PRESSURE_HPA_MIN"] = 760.0
    assert "minimum exceeds mean" in pressure_contract_decision(inverted, "synthetic")[0][0]
