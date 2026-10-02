"""Regression probes reconstructed from NEXT_CODEX_TASK.md; review file unavailable."""

from __future__ import annotations

import pytest

from meteorology.climate_context import RONI_METHOD, RONI_PROVIDER, RONI_SOURCE, select_roni_asof
from meteorology.precipitation_intervals import sum_exact_precipitation_intervals
from meteorology.temporal_products import HOURLY_CORE, summarize_hourly_window, validate_hourly_records


def test_interval_total_rejects_missing_spatial_target() -> None:
    row = dict(interval_start_utc="2024-01-02T00:00:00Z",
               interval_end_utc="2024-01-02T01:00:00Z",
               init_time_utc="2024-01-02T00:00:00Z",
               available_at_utc="2024-01-02T02:00:00Z",
               source_model="HRRR", source_product="sfc", parameter="APCP",
               source_grid_hash="same-grid-for-many-points", step_type="accum", units="mm",
               amount_mm=1.0)
    with pytest.raises(ValueError, match="spatial|target|point|cell"):
        sum_exact_precipitation_intervals([row], start_utc=row["interval_start_utc"],
                                          end_utc=row["interval_end_utc"])

    native = dict(row, spatial_basis="native_grid_point", source_grid_index=7)
    result = sum_exact_precipitation_intervals([native],
                                               start_utc=row["interval_start_utc"],
                                               end_utc=row["interval_end_utc"])
    assert result["spatial_target"] == ("native_grid_point", "same-grid-for-many-points", 7)
    h3 = dict(row, spatial_basis="h3_cell", H3_INDEX="fixture-h3",
              mapping_identity="fixture-crosswalk-hash")
    assert sum_exact_precipitation_intervals([h3],
                                             start_utc=row["interval_start_utc"],
                                             end_utc=row["interval_end_utc"])["spatial_target"][-2:] == (
                                                 "fixture-h3", "fixture-crosswalk-hash")
    second = dict(native, interval_start_utc=row["interval_end_utc"],
                  interval_end_utc="2024-01-02T02:00:00Z",
                  init_time_utc=row["interval_end_utc"],
                  available_at_utc="2024-01-02T03:00:00Z")
    assert sum_exact_precipitation_intervals([native, second],
                                             start_utc=row["interval_start_utc"],
                                             end_utc=second["interval_end_utc"])["interval_count"] == 2
    with pytest.raises(ValueError, match="spatial target"):
        sum_exact_precipitation_intervals([native, dict(second, source_grid_index=8)],
                                          start_utc=row["interval_start_utc"],
                                          end_utc=second["interval_end_utc"])


def test_hourly_f00_rejects_availability_before_valid_time() -> None:
    from meteorology.temporal_products import hourly_utc_instants

    rows = []
    for stamp in hourly_utc_instants("2024-01-02", "America/Los_Angeles"):
        row = {field: 0.0 for field in HOURLY_CORE}
        row.update(valid_time_utc=stamp.isoformat(), init_time_utc=stamp.isoformat(),
                   available_at_utc=stamp.isoformat(), forecast_hour=0,
                   source_model="HRRR", source_product="sfc",
                   availability_policy="assumed_fixed_lag_v1")
        rows.append(row)
    rows[0]["available_at_utc"] = "2024-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="availability|available"):
        validate_hourly_records(rows, local_date="2024-01-02",
                                timezone_name="America/Los_Angeles")
    rows[0]["available_at_utc"] = rows[0]["valid_time_utc"]
    rows[0]["availability_policy"] = "unknown"
    with pytest.raises(ValueError, match="availability policy"):
        validate_hourly_records(rows, local_date="2024-01-02",
                                timezone_name="America/Los_Angeles")


def test_roni_omitted_publication_is_retrospective_only() -> None:
    row = dict(provider=RONI_PROVIDER, method=RONI_METHOD, source_url=RONI_SOURCE,
               year=2024, season="DJF", value_c=0.2,
               retrieved_at_utc="2026-10-02T00:00:00Z")
    assert select_roni_asof([row], year=2024, season="DJF",
                            as_of_utc="2024-04-01T00:00:00Z") is None
    assert select_roni_asof([row], year=2024, season="DJF")["published_at_utc"] is None


def test_hourly_window_rejects_second_offset() -> None:
    with pytest.raises(ValueError, match="boundaries|hour"):
        summarize_hourly_window([], start_utc="2024-01-02T00:00:01Z",
                                end_utc="2024-01-02T01:00:01Z", field="TEMPERATURE_2M_C")
    with pytest.raises(ValueError, match="boundaries|hour"):
        summarize_hourly_window([], start_utc="2024-01-02T00:00:00.000001Z",
                                end_utc="2024-01-02T01:00:00.000001Z",
                                field="TEMPERATURE_2M_C")
