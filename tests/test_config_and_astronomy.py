from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from meteorology.astronomy import (
    local_day_sample_times_utc,
    solar_profile_metrics,
)
from meteorology.config import load_meteorological_config
from meteorology.daylight.compute import (
    compute_daylight_hours,
    solar_day_365,
)
from meteorology.daylight.features import (
    build_daylight_features,
)
from meteorology.lunar.compute import (
    SYNODIC_MONTH_DAYS,
    compute_lunar_illumination_table,
)
from meteorology.lunar.validation import (
    validate_lunar_illumination_features,
)
from meteorology.surface_weather.sampling import (
    make_sample_times_for_local_date,
)


def test_config_is_strict_and_latest_complete_is_timezone_aware(tmp_path: Path) -> None:
    source = yaml.safe_load(Path("config/data/environment_meteorological.yaml").read_text())
    source["unexpected"] = True
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text(yaml.safe_dump(source))
    with pytest.raises(ValueError, match="Unknown keys"):
        load_meteorological_config(config_path)

    config = load_meteorological_config("config/data/environment_meteorological.yaml")
    assert (
        config.surface_weather.resolved_end_date(
            pd.Timestamp("2026-07-28T21:59:00", tz="America/Los_Angeles")
        )
        == "2026-07-27"
    )
    assert (
        config.surface_weather.resolved_end_date(
            pd.Timestamp("2026-07-29T03:00:00", tz="America/Los_Angeles")
        )
        == "2026-07-28"
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda raw: raw["daylight"].update({"timezone": "Not/A_Timezone"}),
            "Unknown daylight timezone",
        ),
        (
            lambda raw: raw["lunar"].update({"start_date": "2025-01-01", "end_date": "2024-01-01"}),
            "lunar.start_date",
        ),
        (
            lambda raw: raw["surface_weather"]["time"].update(
                {"start_date": "2025-01-01", "end_date": "2024-01-01"}
            ),
            "surface_weather.time.start_date",
        ),
        (
            lambda raw: raw["spatial_support"].update({"resolutions": [4, 5, 7]}),
            "exactly",
        ),
        (
            lambda raw: raw["daylight"].update({"h3_resolution": 6}),
            "resolutions are fixed",
        ),
        (
            lambda raw: raw["surface_weather"].update({"bbox_padding_degrees": -0.1}),
            "bbox_padding_degrees",
        ),
        (
            lambda raw: raw["surface_weather"]["sampling"].update({"interval_hours": 3}),
            "six-analysis contract",
        ),
        (
            lambda raw: raw["surface_weather"]["source"].update({"backend": "unknown"}),
            "Unknown keys in surface_weather.source",
        ),
        (
            lambda raw: raw["surface_weather"]["source"].update({"precipitation_forecast_hour": 0}),
            "precipitation_forecast_hour=1",
        ),
        (
            lambda raw: raw["surface_weather"]["raw"].update({"root_dir": ""}),
            "surface_weather.raw.root_dir",
        ),
    ],
)
def test_invalid_contract_settings_fail_closed(tmp_path: Path, mutate, message: str) -> None:
    raw = yaml.safe_load(Path("config/data/environment_meteorological.yaml").read_text())
    mutate(raw)
    path = tmp_path / "invalid-setting.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match=message):
        load_meteorological_config(path)


def test_solar_sampling_preserves_dst_day_lengths() -> None:
    spring = local_day_sample_times_utc("2024-03-10", "America/Los_Angeles", 30)
    fall = local_day_sample_times_utc("2024-11-03", "America/Los_Angeles", 30)
    assert len(spring) == 46
    assert len(fall) == 50
    maximum, mean, low_sun = solar_profile_metrics(
        date="2024-03-10",
        latitudes=[48.5],
        longitudes=[-123.3],
        timezone_name="America/Los_Angeles",
        timestep_minutes=30,
    )
    assert -90 <= maximum[0] <= 90
    assert 0 <= mean[0] <= 90
    assert 0 <= low_sun[0] <= 23
    assert len(make_sample_times_for_local_date("2024-03-10", "America/Los_Angeles", 4)) == 6
    assert len(make_sample_times_for_local_date("2024-11-03", "America/Los_Angeles", 4)) == 6


def test_daylight_formula_and_leap_day_policy_are_characterized() -> None:
    assert compute_daylight_hours(0.0, 81) == pytest.approx(12.0)
    assert solar_day_365(2, 29) == 60
    assert solar_day_365(3, 1) == 60
    cells = pd.DataFrame({"h3": ["a"], "centroid_lat": [48.5], "centroid_lon": [-123.3]})
    characterized = build_daylight_features(cells, "2024-03-10", "2024-03-10")
    assert characterized.loc[0, "daylight_hours"] == pytest.approx(11.272655151173346)
    assert characterized.loc[0, "daylight_fraction"] == pytest.approx(0.46969396463222274)

    leap = build_daylight_features(cells, "2024-02-29", "2024-03-01")
    assert leap["solar_day_365"].tolist() == [60, 60]
    assert leap["is_leap_day"].tolist() == [True, False]
    assert leap["daylight_hours"].nunique() == 1


def test_lunar_kernel_preserves_characterized_legacy_calculations() -> None:
    cells = pd.DataFrame(
        {
            "h3": ["a", "b"],
            "centroid_lat": [48.5, 49.0],
            "centroid_lon": [-123.3, -124.0],
        }
    )
    frame = compute_lunar_illumination_table(
        cells,
        "2024-03-10",
        "2024-03-10",
        timestep_minutes=30,
    ).set_index("h3")

    assert frame.loc["a", "lunar_age_days"] == pytest.approx(0.09421073077667)
    assert frame.loc["a", "lunar_phase_angle_deg"] == pytest.approx(1.148499383078018)
    assert frame.loc["a", "lunar_illumination_fraction"] == pytest.approx(0.00010044810187)
    assert frame.loc["a", "night_hours"] == 10.5
    assert frame.loc["a", "moon_visible_hours"] == 12.0
    assert frame.loc["a", "moon_visible_dark_hours"] == 0.0
    assert frame.loc["b", "moon_visible_dark_hours"] == 0.5
    assert frame.loc["b", "moonlit_dark_hours"] == pytest.approx(0.002921394480416)
    assert frame.loc["b", "moon_visible_dark_fraction"] == pytest.approx(0.047619047619048)
    assert frame.loc["b", "moonlit_dark_fraction"] == pytest.approx(0.000278228045754)
    assert validate_lunar_illumination_features(frame.reset_index(), strict=False) == []


def test_lunar_phase_is_continuous_bounded_and_year_dependent() -> None:
    cells = pd.DataFrame({"h3": ["a"], "centroid_lat": [48.5], "centroid_lon": [-123.3]})
    frame = compute_lunar_illumination_table(
        cells,
        "2024-03-10",
        "2024-03-11",
        timestep_minutes=60,
    )
    ages = frame["lunar_age_days"].to_numpy()
    delta = (ages[1] - ages[0]) % SYNODIC_MONTH_DAYS
    assert delta == pytest.approx(1.0)
    assert frame["lunar_illumination_fraction"].between(0, 1).all()
    assert frame["moonlit_dark_fraction"].between(0, 1).all()

    next_year = compute_lunar_illumination_table(
        cells,
        "2025-03-10",
        "2025-03-10",
        timestep_minutes=60,
    )
    assert next_year.loc[0, "lunar_age_days"] != pytest.approx(ages[0])
