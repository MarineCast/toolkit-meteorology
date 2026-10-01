"""Exact-support geographic acceptance with a small, explicit native grid."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from meteorology.surface_weather.sampling import build_nearest_grid_crosswalk
from meteorology.surface_weather.source import replace_source_grid_precipitation
from meteorology.surface_weather.spatial_acceptance import (
    NativeGrid,
    SpatialAcceptanceError,
    SYNTHETIC_POLICY_ID,
    validate_spatial_acceptance,
)


def _native_and_crop() -> pd.DataFrame:
    lat, lon = np.meshgrid(np.array([47.0, 47.03, 47.06]),
                           np.array([-124.0, -123.96, -123.92]), indexing="ij")
    frame = pd.DataFrame({
        "SOURCE_GRID_INDEX": np.arange(9, dtype="int32"),
        "SOURCE_LAT": lat.ravel(), "SOURCE_LON": lon.ravel(),
        "SOURCE_GRID_HASH": "synthetic-crop-v1",
    })
    frame.attrs["native_grid"] = NativeGrid.from_coordinates(lat, lon)
    frame.attrs["native_indices"] = np.arange(9, dtype="int64")
    return frame


def _support(*rows: tuple[str, float, float]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["H3_INDEX", "CENTROID_LAT", "CENTROID_LON"])


def test_interior_boundary_land_and_water_are_retained() -> None:
    grid = _native_and_crop()
    support = _support(
        ("land", 47.03, -123.96),
        ("water", 47.045, -123.94),
        ("boundary", 47.0, -123.96),
    )
    crosswalk = build_nearest_grid_crosswalk(
        support, grid, policy_id=SYNTHETIC_POLICY_ID
    )
    assert set(crosswalk.H3_INDEX) == {"land", "water", "boundary"}
    assert set(crosswalk.SPATIAL_POLICY_ID) == {SYNTHETIC_POLICY_ID}
    assert set(crosswalk.NATIVE_GRID_CHECKSUM) == {grid.attrs["native_grid"].checksum}
    assert (crosswalk.SOURCE_GRID_DISTANCE_M >= 0).all()


@pytest.mark.parametrize(
    ("support", "reason"),
    [
        (_support(("outside", 47.061, -123.96)), "outside_native_footprint"),
        (_support(("far", 47.045, -123.94)), "native_point_too_distant"),
    ],
)
def test_unsupported_target_has_explicit_reason(
    support: pd.DataFrame, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    grid = _native_and_crop()
    if reason == "native_point_too_distant":
        # Inject a stricter local geometry limit to exercise the independent
        # representativeness outcome without inventing a production cutoff.
        monkeypatch.setattr(NativeGrid, "half_local_diagonal_m", lambda self, index: 1.0)
    with pytest.raises(SpatialAcceptanceError) as error:
        validate_spatial_acceptance(support, grid, policy_id=SYNTHETIC_POLICY_ID)
    assert error.value.report["rejected"][0]["reason"] == reason


def test_truncated_crop_and_missing_native_pixel_are_distinct_from_domain() -> None:
    grid = _native_and_crop()
    support = _support(("target", 47.03, -123.96))
    for retained in ([0, 1, 2], [0, 1, 2, 3, 5, 6, 7, 8]):
        crop = grid.iloc[retained].reset_index(drop=True)
        crop.attrs["native_grid"] = grid.attrs["native_grid"]
        crop.attrs["native_indices"] = np.asarray(retained, dtype="int64")
        with pytest.raises(SpatialAcceptanceError) as error:
            build_nearest_grid_crosswalk(support, crop, policy_id=SYNTHETIC_POLICY_ID)
        assert error.value.report["rejected"][0]["reason"] == "native_nearest_missing_from_crop"


def test_missing_native_metadata_or_coordinate_mismatch_fails_closed() -> None:
    grid = _native_and_crop()
    support = _support(("target", 47.03, -123.96))
    unproven = grid.copy()
    unproven.attrs.clear()
    with pytest.raises(ValueError, match="native-grid coordinates"):
        validate_spatial_acceptance(support, unproven, policy_id=SYNTHETIC_POLICY_ID)
    grid.loc[4, "SOURCE_LAT"] = np.nan
    with pytest.raises(ValueError, match="do not match native-grid"):
        validate_spatial_acceptance(support, grid, policy_id=SYNTHETIC_POLICY_ID)


def test_conus_policy_rejects_unverified_native_shape() -> None:
    grid = _native_and_crop()
    with pytest.raises(ValueError, match="differs from NOAA CONUS"):
        validate_spatial_acceptance(_support(("target", 47.03, -123.96)), grid)


def test_matched_crop_cannot_hide_different_f01_native_grid() -> None:
    core = _native_and_crop()
    precipitation = core.copy()
    other_lat = core.attrs["native_grid"].lat.copy()
    other_lat[0, 0] += 0.001
    precipitation.attrs["native_grid"] = NativeGrid.from_coordinates(
        other_lat, core.attrs["native_grid"].lon
    )
    with pytest.raises(ValueError, match="full native HRRR grids do not match"):
        replace_source_grid_precipitation(core, precipitation)
