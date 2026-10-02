from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from meteorology.maintenance.validate_astronomy import compare_usno_reference
from meteorology.maintenance.validate_horizons import compare_horizons_reference
from meteorology.maintenance.validate_ndbc import _observations, compare_ndbc_day


REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "validation" / "references"


def test_frozen_usno_cases_cover_seasons_dst_leap_and_polar_days() -> None:
    payload = json.loads((REFERENCE_ROOT / "usno_2024_solar_lunar.json").read_text())
    report = compare_usno_reference(payload)
    assert report["daylight_hours"]["count"] == 10
    assert report["lunar_illumination_fraction"]["count"] == 10
    assert {case["stratum"] for case in report["cases"]} == {"regional", "polar"}
    assert report["daylight_hours"]["passes_prespecified_limits"]
    assert report["lunar_illumination_fraction"]["passes_prespecified_limits"]
    offsets = {
        case["date"]: case["product_sample_minus_reference_noon_hours"]
        for case in report["cases"] if case["stratum"] == "regional"
    }
    assert offsets["2024-03-10"] == -7.0
    assert offsets["2024-11-03"] == -8.0


def test_usno_comparison_rejects_reference_identity_mismatch() -> None:
    payload = json.loads((REFERENCE_ROOT / "usno_2024_solar_lunar.json").read_text())
    bad = copy.deepcopy(payload)
    bad["cases"][0]["response"]["geometry"]["coordinates"][0] += 1.0
    with pytest.raises(ValueError, match="coordinates differ"):
        compare_usno_reference(bad)


def test_frozen_horizons_altitudes_cover_sun_moon_and_dark_visible_hours() -> None:
    payload = json.loads((REFERENCE_ROOT / "jpl_horizons_2024_altitudes.json").read_text())
    report = compare_horizons_reference(payload)
    assert {body: metric["count"] for body, metric in report["metrics"].items()} == {
        "sun": 144, "moon": 144,
    }
    assert all(metric["passes_prespecified_limits"] for metric in report["metrics"].values())
    assert len(report["dark_visible_hourly"]) == 6
    assert all(case["hourly_count_difference"] == 0 for case in report["dark_visible_hourly"])


def test_horizons_comparison_rejects_query_identity_mismatch() -> None:
    payload = json.loads((REFERENCE_ROOT / "jpl_horizons_2024_altitudes.json").read_text())
    bad = copy.deepcopy(payload)
    bad["cases"][0]["url"] = bad["cases"][0]["url"].replace("AIRLESS", "REFRACTED")
    with pytest.raises(ValueError, match="request identity"):
        compare_horizons_reference(bad)


def test_ndbc_comparison_records_missingness_and_height_limitations() -> None:
    observations = (REFERENCE_ROOT / "ndbc_46088_2024-01-02.txt").read_text()
    metadata = json.loads((REFERENCE_ROOT / "ndbc_46088_2024-01-02.meta.json").read_text())
    rows = [{
        "valid_time_utc": "2024-01-02T00:00:00+00:00",
        "temperature_2m_k": 279.15,
        "mean_sea_level_pressure_pa": 102320.0,
        "u_wind_10m_ms": -0.8,
        "v_wind_10m_ms": 0.0,
        "wind_gust_surface_ms": 2.3,
        "source_grid_distance_m": 1500.0,
    }]
    report = compare_ndbc_day(observations, rows, date="2024-01-02", station_metadata=metadata)
    assert report["observation_hours"] == 24
    assert report["model_hours"] == report["matched_hours"] == 1
    assert report["metrics"]["mean_sea_level_pressure_hpa"]["mae"] == 0.0
    assert report["threshold_decision"] == "not_set_for_one_station_day"
    assert any("3.8 m" in limitation for limitation in report["limitations"])


def test_ndbc_historical_field_specific_missing_tokens_do_not_enter_metrics() -> None:
    text = """#YY MM DD hh mm WDIR WSPD GST PRES ATMP
#yr mo dy hr mn degT m/s m/s hPa degC
2024 01 02 00 00 999 99.0 99.0 9999.0 999.0
2024 01 02 01 00 0 0.0 0.0 999.0 9.0
"""
    rejected = []
    rows = _observations(text, "2024-01-02", rejected=rejected)
    assert rows["2024-01-02T00:00:00+00:00"] == dict.fromkeys(
        ("ATMP", "PRES", "WSPD", "GST", "WDIR")
    )
    assert rows["2024-01-02T01:00:00+00:00"]["WSPD"] == 0.0
    assert rows["2024-01-02T01:00:00+00:00"]["PRES"] == 999.0
    assert {item["field"] for item in rejected} == {"ATMP", "PRES", "WSPD", "GST", "WDIR"}
    assert all(item["reason"] == "source_missing_token" for item in rejected)


def test_ndbc_realtime_text_and_malformed_tokens_have_explicit_disposition() -> None:
    text = """#YY MM DD hh mm WDIR WSPD GST PRES ATMP
#yr mo dy hr mn degT m/s m/s hPa degC
2024 01 02 00 00 MM MM MM MM MM
2024 01 02 01 00 180 nan broken 1013.0 9.0
2024 01 03 00 00 180 2.0 3.0 1013.0 9.0
"""
    rejected = []
    rows = _observations(text, "2024-01-02", source_format="realtime_stdmet",
                         rejected=rejected)
    assert len(rows) == 2
    assert {item["reason"] for item in rejected} == {
        "source_missing_token", "non_finite", "malformed_token",
        "out_of_period_or_not_exact_hour",
    }
    assert rows["2024-01-02T01:00:00+00:00"]["GST"] is None
    with pytest.raises(ValueError, match="Duplicate NDBC"):
        _observations(text.replace("2024 01 03 00 00", "2024 01 02 01 00"),
                      "2024-01-02", source_format="realtime_stdmet")
