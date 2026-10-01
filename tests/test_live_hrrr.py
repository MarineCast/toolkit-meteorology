"""Explicitly opt-in provider compatibility smoke; ordinary CI skips it."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from meteorology.surface_weather.source import (
    fetch_cropped_hrrr_grid,
    fetch_cropped_hrrr_precip_grid,
)


@pytest.mark.live
def test_one_hrrr_cycle_and_matched_precipitation() -> None:
    if os.environ.get("METEOROLOGY_LIVE_HRRR") != "1":
        pytest.skip("Set METEOROLOGY_LIVE_HRRR=1 to opt into NOAA/Herbie acquisition.")
    valid = pd.Timestamp("2024-01-02T12:00:00Z")
    bbox = {"min_lat": 48.4, "max_lat": 48.5, "min_lon": -123.3, "max_lon": -123.2}
    core, core_uri = fetch_cropped_hrrr_grid(
        valid_time_utc=valid, bbox=bbox, bbox_padding_degrees=0.0,
        availability_lag_hours=6,
    )
    precip, precip_uri = fetch_cropped_hrrr_precip_grid(
        valid_time_utc=valid, bbox=bbox, bbox_padding_degrees=0.0,
        availability_lag_hours=6, forecast_hour=1,
    )
    assert core_uri and precip_uri and core_uri != precip_uri
    assert not core.empty and len(core) == len(precip)
    assert set(core["SOURCE_GRID_HASH"]) == set(precip["SOURCE_GRID_HASH"])
    assert np.array_equal(core["SOURCE_GRID_INDEX"], precip["SOURCE_GRID_INDEX"])
    assert np.isfinite(core["TEMPERATURE_2M_K"]).all()
    assert np.isfinite(precip["PRECIP_RATE_KG_M2_S"]).all()
    assert (precip["PRECIP_RATE_KG_M2_S"] >= 0).all()
    assert set(precip["PRECIP_FORECAST_HOUR"]) == {1}
