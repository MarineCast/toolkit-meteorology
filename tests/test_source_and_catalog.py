from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
import yaml

from meteorology.surface_weather.sampling import (
    build_nearest_grid_crosswalk,
)
from meteorology.surface_weather.source import (
    hrrr_aws_archive_uri,
    normalize_forecast_precip_grid,
    normalize_hrrr_values,
)
from meteorology.surface_weather.source_validation import (
    infer_required_hrrr_variables,
)


def test_hrrr_unit_normalization_is_explicit() -> None:
    assert normalize_hrrr_values(pd.Series([10.0]), variable="temperature_2m_k", units="degC").iloc[
        0
    ] == pytest.approx(283.15)
    assert normalize_hrrr_values(
        pd.Series([1000.0]), variable="mean_sea_level_pressure_pa", units="hPa"
    ).iloc[0] == pytest.approx(100_000.0)
    assert normalize_hrrr_values(
        pd.Series([3.6]), variable="precip_rate_kg_m2_s", units="mm/h"
    ).iloc[0] == pytest.approx(0.001)
    with pytest.raises(ValueError, match="Unsupported HRRR units"):
        normalize_hrrr_values(pd.Series([1.0]), variable="wind_gust_surface_ms", units="knots")


def test_hrrr_archive_uri_identifies_the_direct_f00_object() -> None:
    assert hrrr_aws_archive_uri(pd.Timestamp("2024-01-02T08:00:00Z")) == (
        "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/"
        "hrrr.20240102/conus/hrrr.t08z.wrfsfcf00.grib2"
    )
    assert hrrr_aws_archive_uri(pd.Timestamp("2024-01-02T08:00:00Z"), 1) == (
        "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/"
        "hrrr.20240102/conus/hrrr.t07z.wrfsfcf01.grib2"
    )


def test_f01_precipitation_grid_retains_distinct_forecast_provenance() -> None:
    valid = pd.Timestamp("2024-01-02T08:00:00Z")
    frame = normalize_forecast_precip_grid(
        pd.DataFrame(
            {
                "hrrr_lat": [48.0, 48.1],
                "hrrr_lon": [-124.0, -123.9],
                "precip_rate_kg_m2_s": [0.0, 0.001],
            }
        ),
        units="kg m**-2 s**-1",
        valid_time_utc=valid,
        forecast_hour=1,
    )
    assert set(frame["PRECIP_VALID_TIME_UTC"]) == {valid.isoformat()}
    assert set(frame["PRECIP_INIT_TIME_UTC"]) == {(valid - pd.Timedelta(hours=1)).isoformat()}
    assert set(frame["PRECIP_FORECAST_HOUR"]) == {1}
    assert frame["PRECIP_RATE_KG_M2_S"].max() == pytest.approx(0.001)


def _temperature_dataset(variable_names: tuple[str, ...]) -> xr.Dataset:
    values = np.ones((2, 2), dtype=float)
    dataset = xr.Dataset(
        {name: (("y", "x"), values.copy()) for name in variable_names},
        coords={
            "latitude": (("y", "x"), [[48.0, 48.0], [49.0, 49.0]]),
            "longitude": (("y", "x"), [[-124.0, -123.0], [-124.0, -123.0]]),
        },
    )
    for name in variable_names:
        dataset[name].attrs.update(
            {
                "GRIB_shortName": "tmp",
                "GRIB_name": "Temperature",
                "GRIB_typeOfLevel": "heightAboveGround",
                "GRIB_level": 2,
                "units": "K",
            }
        )
    return dataset


def test_hrrr_variable_matching_fails_on_missing_or_ambiguous_identity() -> None:
    searches = {"temperature_2m_k": ":TMP:2 m above ground:"}
    dataset = _temperature_dataset(("t2m",))
    mapping = infer_required_hrrr_variables(dataset, searches, reject_ambiguous=True)
    assert mapping["temperature_2m_k"] is not None
    assert mapping["temperature_2m_k"][1] == "t2m"

    wrong_level = _temperature_dataset(("t2m",))
    wrong_level["t2m"].attrs["GRIB_level"] = 10
    with pytest.raises(ValueError, match="Strict HRRR selector mismatch"):
        infer_required_hrrr_variables(wrong_level, searches, reject_ambiguous=True)

    with pytest.raises(ValueError, match="Could not infer HRRR variable"):
        infer_required_hrrr_variables(
            dataset,
            {"visibility_m": ":VIS:surface:"},
            reject_ambiguous=True,
        )
    with pytest.raises(ValueError, match="Ambiguous HRRR variable mapping"):
        infer_required_hrrr_variables(
            _temperature_dataset(("temperature_a", "temperature_b")),
            searches,
            reject_ambiguous=True,
        )


def test_nearest_grid_crosswalk_is_source_row_order_invariant() -> None:
    support = pd.DataFrame(
        {
            "H3_INDEX": ["a", "b"],
            "CENTROID_LAT": [48.01, 48.99],
            "CENTROID_LON": [-123.99, -123.01],
        }
    )
    raw = pd.DataFrame(
        {
            "SOURCE_GRID_INDEX": [11, 22],
            "SOURCE_LAT": [48.0, 49.0],
            "SOURCE_LON": [-124.0, -123.0],
            "SOURCE_GRID_HASH": ["fixture", "fixture"],
        }
    )
    first = (
        build_nearest_grid_crosswalk(support, raw).sort_values("H3_INDEX").reset_index(drop=True)
    )
    second = (
        build_nearest_grid_crosswalk(support, raw.iloc[::-1].reset_index(drop=True))
        .sort_values("H3_INDEX")
        .reset_index(drop=True)
    )
    pd.testing.assert_frame_equal(first, second)


def test_environment_catalog_never_promotes_metadata_to_features() -> None:
    catalog = yaml.safe_load(
        Path("config/feature_catalog.yaml").read_text()
    )
    assert catalog["catalog_id"] == "environment"
    assert catalog["family_counts"]["meteorological"] > 0
    for product in catalog["products"].values():
        for field in product.get("features", {}).values():
            if field["role"] in {
                "coverage",
                "provenance_or_qc",
                "bookkeeping",
                "identifier",
                "evidence",
                "support_or_qc",
            }:
                assert field["variable_kind"] == "metadata"
    weather = catalog["products"]["surface_weather_daily"]["features"]
    assert "PRECIP_MM_DAY_ESTIMATE" in weather
    assert "STORM_SEVERITY_INDEX_MAX" not in weather
    assert "atmospheric_visibility_daily" not in catalog["products"]
    assert "surface_weather_subdaily" not in catalog["products"]


def test_core_catalog_declares_meteorological_partition_contracts() -> None:
    from meteorology.core.data import DATASETS

    expected = {
        "environment.meteorological.surface_weather.h3_samples_r5": ("YEAR", "DATE"),
        "environment.meteorological.surface_weather.h3_daily_r5": ("YEAR", "DATE"),
        "environment.meteorological.daylight.h3_daily_r4": ("YEAR",),
        "environment.meteorological.lunar.h3_daily_r5": ("YEAR",),
        "environment.meteorological.surface_weather.h3_crosswalk_r5": ("SOURCE_GRID_HASH",),
    }
    for dataset_id, partition_keys in expected.items():
        assert DATASETS.get(dataset_id).partition_keys == partition_keys

