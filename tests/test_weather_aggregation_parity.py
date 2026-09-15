"""Exact parity and fail-closed contracts for batched daily aggregation."""

import numpy as np
import pandas as pd
import pytest

from meteorology.surface_weather.build import (
    DAILY_SCHEMA,
    aggregate_surface_weather_daily,
)

METRICS = {
    "TEMPERATURE_2M_C": ("mean",),
    "RELATIVE_HUMIDITY_2M_PCT": ("mean",),
    "WIND_SPEED_10M_MS": ("mean", "max"),
    "WIND_GUST_SURFACE_MS": ("mean", "max"),
    "VISIBILITY_KM": ("mean", "min"),
    "TOTAL_CLOUD_COVER_PCT": ("mean",),
    "MEAN_SEA_LEVEL_PRESSURE_HPA": ("mean", "min"),
    "SOURCE_GRID_DISTANCE_M": ("mean", "max"),
}


def _samples(interval: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rows = []
    for date in ("2024-03-10", "2024-11-03"):
        for cell in ("c", "a", "b"):
            for hour in range(0, 24, interval):
                valid = pd.Timestamp(date, tz="UTC") + pd.Timedelta(hours=hour)
                rows.append(
                    {
                        "H3_INDEX": cell,
                        "DATE": date,
                        "VALID_TIME_UTC": valid.isoformat(),
                        "AVAILABLE_AT_UTC": (valid + pd.Timedelta(hours=6)).isoformat(),
                        "SOURCE_DATA_STATE": "COMPLETE",
                        **{column: rng.normal() * 10 ** rng.uniform(-8, 8) for column in METRICS},
                        "PRECIP_RATE_MM_HR": rng.uniform(0, 10),
                    }
                )
    return pd.DataFrame(rows).sample(frac=1, random_state=17)


def _per_group_reference(samples: pd.DataFrame, interval: int) -> pd.DataFrame:
    """Preserve the former pandas-Series reductions as an independent oracle."""
    rows = []
    for (cell, date), group in samples.groupby(["H3_INDEX", "DATE"], sort=True):
        group = group.sort_values("VALID_TIME_UTC")
        row = {"H3_INDEX": str(cell), "DATE": str(date)}
        for column, reducers in METRICS.items():
            values = pd.to_numeric(group[column], errors="raise").astype(float)
            for reducer in reducers:
                row[f"{column}_{reducer.upper()}"] = float(getattr(values, reducer)())
        precip = pd.to_numeric(group.PRECIP_RATE_MM_HR, errors="raise").astype(float)
        row.update(
            {
                "PRECIP_MM_DAY_ESTIMATE": float((precip * float(interval)).sum()),
                "EXPECTED_SAMPLE_COUNT": 24 // interval,
                "SAMPLE_COUNT": 24 // interval,
                "SAMPLE_COVERAGE_FRAC": 1.0,
                "FIRST_VALID_TIME_UTC": str(group.VALID_TIME_UTC.min()),
                "LAST_VALID_TIME_UTC": str(group.VALID_TIME_UTC.max()),
                "LATEST_AVAILABLE_AT_UTC": str(group.AVAILABLE_AT_UTC.max()),
                "QC_STATE": "COMPLETE",
            }
        )
        rows.append(row)
    return pd.DataFrame(rows, columns=DAILY_SCHEMA.names)


@pytest.mark.parametrize("interval", [1, 4, 6])
@pytest.mark.parametrize("dtype", ["float64", "float32", "str"])
def test_aggregation_exactly_matches_per_group_reference(interval: int, dtype: str) -> None:
    samples = _samples(interval)
    for column in [*METRICS, "PRECIP_RATE_MM_HR"]:
        samples[column] = samples[column].astype(dtype)
    expected = _per_group_reference(samples, interval)
    actual = aggregate_surface_weather_daily(samples, interval)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_aggregation_rejects_nonfinite_values(value: float) -> None:
    samples = _samples()
    samples.loc[samples.index[0], "PRECIP_RATE_MM_HR"] = value
    with pytest.raises(ValueError, match="Non-finite"):
        aggregate_surface_weather_daily(samples, 4)


def test_aggregation_rejects_incomplete_group() -> None:
    with pytest.raises(ValueError, match="Incomplete"):
        aggregate_surface_weather_daily(_samples().iloc[1:], 4)


@pytest.mark.parametrize("state", ["UNAVAILABLE", None, pd.NA])
def test_aggregation_rejects_incomplete_state(state: object) -> None:
    samples = _samples()
    samples.loc[samples.index[0], "SOURCE_DATA_STATE"] = state
    with pytest.raises(ValueError, match="Incomplete"):
        aggregate_surface_weather_daily(samples, 4)


def test_aggregation_rejects_duplicate_valid_time() -> None:
    samples = _samples()
    rows = samples.loc[samples.H3_INDEX.eq("a") & samples.DATE.eq("2024-03-10")].index
    samples.loc[rows[0], "VALID_TIME_UTC"] = samples.loc[rows[1], "VALID_TIME_UTC"]
    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_surface_weather_daily(samples, 4)


@pytest.mark.parametrize("column", ["H3_INDEX", "DATE", "VALID_TIME_UTC", "AVAILABLE_AT_UTC"])
def test_aggregation_rejects_missing_keys_or_times(column: str) -> None:
    samples = _samples()
    samples.loc[samples.index[0], column] = None
    with pytest.raises(ValueError, match="missing keys or timestamps"):
        aggregate_surface_weather_daily(samples, 4)


def test_aggregation_retains_empty_schema() -> None:
    actual = aggregate_surface_weather_daily(_samples().iloc[:0], 4)
    pd.testing.assert_frame_equal(actual, pd.DataFrame(columns=DAILY_SCHEMA.names))
