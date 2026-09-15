from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from meteorology.modeling.feature_policy import (
    apply_feature_policy,
    build_feature_policy,
    load_feature_policy,
)


def test_policy_excludes_metadata_observer_effort_and_deterministic_aliases() -> None:
    catalog = yaml.safe_load(
        Path("config/feature_catalog.yaml").read_text()
    )
    policy = build_feature_policy(catalog)
    by_key = {(row["product"], row["column"]): row for row in policy["features"]}
    assert not by_key[("surface_weather_daily", "SAMPLE_COVERAGE_FRAC")]["included_by_default"]
    assert ("atmospheric_visibility_daily", "BLENDED_VIEWABILITY_SCORE_MEAN") not in by_key
    assert by_key[("surface_weather_daily", "PRECIP_MM_DAY_ESTIMATE")]["included_by_default"]
    assert not by_key[("daylight_daily", "DAYLIGHT_FRACTION")]["included_by_default"]
    assert by_key[("daylight_daily", "DAYLIGHT_HOURS")]["included_by_default"]
    assert not by_key[("lunar_daily", "WEIGHT_LUNAR_ILLUMINATION")]["included_by_default"]


def test_checked_in_policy_matches_catalog_and_applies_prefixed_columns() -> None:
    policy = load_feature_policy()
    selected = [row for row in policy["features"] if row["included_by_default"]]
    frame = pd.DataFrame(
        {
            "H3_INDEX": ["cell"],
            "DATE": ["2024-01-01"],
            **{f"{row['product']}__{row['column']}": [1.0] for row in selected},
        }
    )
    result = apply_feature_policy(frame, policy)
    assert list(result.columns[:2]) == ["H3_INDEX", "DATE"]
    assert result.shape[1] == len({(row["product"], row["column"]) for row in selected}) + 2


def test_declared_deterministic_aliases_are_exact_in_producer_outputs() -> None:
    from meteorology.daylight.features import (
        build_daylight_features,
    )
    from meteorology.lunar.compute import (
        SYNODIC_MONTH_DAYS,
        compute_lunar_illumination_table,
    )

    cells = pd.DataFrame({"h3": ["fixture"], "centroid_lat": [48.5], "centroid_lon": [-123.3]})
    daylight = build_daylight_features(cells, "2024-01-01", "2024-01-03")
    assert np.array_equal(
        daylight["daylight_fraction"].to_numpy(),
        (daylight["daylight_hours"] / 24.0).to_numpy(),
    )
    assert np.array_equal(
        daylight["daylight_weight"].to_numpy(), daylight["daylight_fraction"].to_numpy()
    )
    lunar = compute_lunar_illumination_table(
        cells,
        "2024-01-01",
        "2024-01-02",
        timezone_name="America/Los_Angeles",
        timestep_minutes=60,
    )
    assert np.allclose(
        lunar["lunar_phase_angle_deg"],
        np.mod(360.0 * lunar["lunar_age_days"] / SYNODIC_MONTH_DAYS, 360.0),
    )
    assert np.array_equal(
        lunar["weight_lunar_illumination"].to_numpy(),
        lunar["lunar_illumination_fraction"].to_numpy(),
    )
    assert np.array_equal(
        lunar["weight_moonlit_dark_hours"].to_numpy(),
        lunar["moonlit_dark_fraction"].to_numpy(),
    )
