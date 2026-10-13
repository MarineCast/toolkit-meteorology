from __future__ import annotations

from datetime import timedelta

import pytest

from meteorology.temporal_products import local_day_bounds
from meteorology.precipitation_intervals import (
    difference_same_run_accumulations, sum_exact_precipitation_intervals,
)

def _intervals(date: str) -> list[dict]:
    start, end = local_day_bounds(date, "America/Los_Angeles")
    rows = []
    cursor = start
    while cursor < end:
        following = cursor + timedelta(hours=1)
        rows.append(dict(interval_start_utc=cursor.isoformat(), interval_end_utc=following.isoformat(),
                         init_time_utc=cursor.isoformat(), available_at_utc=following.isoformat(),
                         source_model="HRRR", source_product="sfc", parameter="APCP",
                         source_grid_hash="fixture-grid", source_grid_index=8,
                         spatial_basis="native_grid_point", step_type="accum", units="mm",
                         product_kind="retrospective_accumulation",
                         amount_mm=0.0))
        cursor = following
    return rows


@pytest.mark.parametrize("date,count", [
    ("2024-03-10", 23), ("2024-01-02", 24), ("2024-11-03", 25),
])
def test_exact_precipitation_amount_handles_dry_and_dst(date: str, count: int) -> None:
    start, end = local_day_bounds(date, "America/Los_Angeles")
    rows = _intervals(date)
    rows[count // 2]["amount_mm"] = 3.25
    report = sum_exact_precipitation_intervals(rows, start_utc=start.isoformat(),
                                                end_utc=end.isoformat())
    assert report["covered_hours"] == count
    assert report["modeled_precipitation_amount_mm"] == 3.25


def test_precipitation_gap_overlap_source_switch_and_negative_block_strict_total() -> None:
    start, end = local_day_bounds("2024-01-02", "America/Los_Angeles")
    rows = _intervals("2024-01-02")
    for changed in (
        rows[:-1], rows[:1] + rows[2:], rows + [rows[-1]],
        [*rows[:5], dict(rows[5], source_grid_hash="other"), *rows[6:]],
        [*rows[:5], dict(rows[5], amount_mm=-0.01), *rows[6:]],
        [*rows[:5], dict(rows[5], step_type="instant"), *rows[6:]],
    ):
        with pytest.raises(ValueError):
            sum_exact_precipitation_intervals(changed, start_utc=start.isoformat(),
                                               end_utc=end.isoformat())


def test_cumulative_difference_requires_one_run_and_verified_step_bounds() -> None:
    base = dict(source_model="HRRR", source_product="sfc", parameter="APCP",
                source_grid_hash="fixture-grid", source_grid_index=8,
                spatial_basis="native_grid_point",
                init_time_utc="2024-01-02T00:00:00+00:00", step_type="accum",
                units="mm", step_start_hour=0)
    earlier = dict(base, valid_time_utc="2024-01-02T01:00:00+00:00",
                   available_at_utc="2024-01-02T02:00:00+00:00", step_end_hour=1, amount_mm=2.0)
    later = dict(base, valid_time_utc="2024-01-02T03:00:00+00:00",
                 available_at_utc="2024-01-02T04:00:00+00:00", step_end_hour=3, amount_mm=5.5)
    result = difference_same_run_accumulations(earlier, later)
    assert result["modeled_precipitation_amount_mm"] == 3.5
    assert result["step_start_hour"] == 1
    assert result["available_at_utc"] == "2024-01-02T04:00:00+00:00"
    assert result["product_kind"] == "forecast_accumulation"
    assert sum_exact_precipitation_intervals([result],
                                             start_utc=result["interval_start_utc"],
                                             end_utc=result["interval_end_utc"])[
                                                 "modeled_precipitation_amount_mm"] == 3.5
    for changed in (dict(later, init_time_utc="2024-01-02T01:00:00+00:00"),
                    dict(later, amount_mm=1.0),
                    dict(later, step_start_hour=1),
                    dict(later, step_end_hour=2),
                    dict(later, source_grid_index=9)):
        with pytest.raises(ValueError):
            difference_same_run_accumulations(earlier, changed)


def test_future_forecast_interval_uses_publication_and_asof_not_valid_end() -> None:
    base = dict(source_model="HRRR", source_product="sfc", parameter="APCP",
                source_grid_hash="grid", source_grid_index=8,
                spatial_basis="native_grid_point", init_time_utc="2024-01-02T00:00:00Z",
                step_type="accum", units="mm", step_start_hour=0,
                available_at_utc="2024-01-02T00:20:00Z")
    first = dict(base, valid_time_utc="2024-01-02T01:00:00Z", step_end_hour=1,
                 amount_mm=1.0)
    second = dict(base, valid_time_utc="2024-01-02T02:00:00Z", step_end_hour=2,
                  amount_mm=3.0)
    interval = difference_same_run_accumulations(first, second)
    assert sum_exact_precipitation_intervals(
        [interval], start_utc=interval["interval_start_utc"],
        end_utc=interval["interval_end_utc"], as_of_utc="2024-01-02T00:30:00Z",
    )["modeled_precipitation_amount_mm"] == 2.0
    with pytest.raises(ValueError, match="as-of"):
        sum_exact_precipitation_intervals(
            [interval], start_utc=interval["interval_start_utc"],
            end_utc=interval["interval_end_utc"], as_of_utc="2024-01-02T00:10:00Z")
    with pytest.raises(ValueError, match="initialization"):
        difference_same_run_accumulations(dict(first, available_at_utc="2024-01-01T23:59:00Z"),
                                          second)
    with pytest.raises(ValueError, match="interval end"):
        sum_exact_precipitation_intervals(
            [dict(interval, product_kind="retrospective_accumulation")],
            start_utc=interval["interval_start_utc"], end_utc=interval["interval_end_utc"])
    next_cycle = dict(interval, interval_start_utc=interval["interval_end_utc"],
                      interval_end_utc="2024-01-02T03:00:00Z",
                      init_time_utc="2024-01-02T01:00:00Z",
                      available_at_utc="2024-01-02T01:20:00Z", amount_mm=4.0)
    assert sum_exact_precipitation_intervals(
        [interval, next_cycle], start_utc=interval["interval_start_utc"],
        end_utc=next_cycle["interval_end_utc"],
        as_of_utc="2024-01-02T01:30:00Z")["modeled_precipitation_amount_mm"] == 6.0


@pytest.mark.parametrize("changes", [
    {"step_start_hour": 0, "step_end_hour": 6},
    {"step_start_hour": 0},
    {"step_start_hour": False, "step_end_hour": 1},
    {"step_start_hour": -1, "step_end_hour": 1},
    {"valid_time_utc": "2024-01-02T20:00:00Z"},
])
def test_interval_total_rejects_contradictory_decoded_metadata(changes) -> None:
    row = _intervals("2024-01-02")[0]
    with pytest.raises(ValueError, match="step bounds|valid time"):
        sum_exact_precipitation_intervals([dict(row, **changes)],
                                         start_utc=row["interval_start_utc"],
                                         end_utc=row["interval_end_utc"])
