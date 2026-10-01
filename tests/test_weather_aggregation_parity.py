"""Exact parity and fail-closed contracts for batched daily aggregation."""

import numpy as np
import pandas as pd
import pytest

from meteorology.surface_weather.build import (
    DAILY_SCHEMA,
    aggregate_surface_weather_daily,
)
from meteorology.surface_weather.wind import validate_daily_wind_vectors

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
                        "U_WIND_10M_MS": rng.normal(),
                        "V_WIND_10M_MS": rng.normal(),
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
    return pd.DataFrame(rows)


@pytest.mark.parametrize("interval", [1, 4, 6])
@pytest.mark.parametrize("dtype", ["float64", "float32", "str"])
def test_aggregation_exactly_matches_per_group_reference(interval: int, dtype: str) -> None:
    samples = _samples(interval)
    for column in [*METRICS, "PRECIP_RATE_MM_HR"]:
        samples[column] = samples[column].astype(dtype)
    expected = _per_group_reference(samples, interval)
    actual = aggregate_surface_weather_daily(samples, interval)
    pd.testing.assert_frame_equal(actual[expected.columns], expected, check_exact=True)


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


def test_wind_direction_wraparound_and_calm_are_not_arithmetic_angles() -> None:
    samples = _samples().query("H3_INDEX == 'a' and DATE == '2024-03-10'").copy()
    degrees = np.array([359.0, 1.0] * 3)
    samples["U_WIND_10M_MS"] = -np.sin(np.deg2rad(degrees))
    samples["V_WIND_10M_MS"] = -np.cos(np.deg2rad(degrees))
    result = aggregate_surface_weather_daily(samples, 4).iloc[0]
    assert result.WIND_DIRECTION_FROM_10M_DEG == pytest.approx(0.0, abs=1e-10)
    assert result.WIND_VECTOR_SPEED_10M_MS == pytest.approx(np.cos(np.deg2rad(1)))

    samples["U_WIND_10M_MS"] = [1.0, -1.0] * 3
    samples["V_WIND_10M_MS"] = 0.0
    calm = aggregate_surface_weather_daily(samples, 4).iloc[0]
    assert calm.WIND_VECTOR_SPEED_10M_MS == 0.0
    assert pd.isna(calm.WIND_DIRECTION_FROM_10M_DEG)


def test_shared_daily_wind_rule_pairs_calm_and_noncalm_nulls() -> None:
    samples = _samples().query("H3_INDEX == 'a' and DATE == '2024-03-10'").copy()
    active = aggregate_surface_weather_daily(samples, 4)
    validate_daily_wind_vectors(active)
    missing = active.copy()
    missing["WIND_DIRECTION_FROM_10M_DEG"] = np.nan
    with pytest.raises(ValueError, match="null exactly for calm"):
        validate_daily_wind_vectors(missing)
    wrong_angle = active.copy()
    wrong_angle["WIND_DIRECTION_FROM_10M_DEG"] = (
        wrong_angle["WIND_DIRECTION_FROM_10M_DEG"] + 10.0
    ) % 360.0
    with pytest.raises(ValueError, match="differs from its mean components"):
        validate_daily_wind_vectors(wrong_angle)
    samples["U_WIND_10M_MS"] = [1.0, -1.0] * 3
    samples["V_WIND_10M_MS"] = 0.0
    calm = aggregate_surface_weather_daily(samples, 4)
    validate_daily_wind_vectors(calm)
    invented = calm.copy()
    invented["WIND_DIRECTION_FROM_10M_DEG"] = 0.0
    with pytest.raises(ValueError, match="null exactly for calm"):
        validate_daily_wind_vectors(invented)


def test_precipitation_uses_each_snapshot_once() -> None:
    samples = _samples().query("H3_INDEX == 'a' and DATE == '2024-03-10'").copy()
    samples["PRECIP_RATE_MM_HR"] = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    result = aggregate_surface_weather_daily(samples, 4).iloc[0]
    assert result.PRECIP_MM_DAY_ESTIMATE == 4.0
