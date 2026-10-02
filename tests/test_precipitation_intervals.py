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
                         source_grid_hash="fixture-grid", step_type="accum", units="mm",
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
                init_time_utc="2024-01-02T00:00:00+00:00", step_type="accum",
                units="mm", step_start_hour=0)
    earlier = dict(base, valid_time_utc="2024-01-02T01:00:00+00:00",
                   step_end_hour=1, amount_mm=2.0)
    later = dict(base, valid_time_utc="2024-01-02T03:00:00+00:00",
                 step_end_hour=3, amount_mm=5.5)
    result = difference_same_run_accumulations(earlier, later)
    assert result["modeled_precipitation_amount_mm"] == 3.5
    assert result["step_start_hour"] == 1
    for changed in (dict(later, init_time_utc="2024-01-02T01:00:00+00:00"),
                    dict(later, amount_mm=1.0),
                    dict(later, step_start_hour=1),
                    dict(later, step_end_hour=2),
                    dict(later, source_grid_index=9)):
        with pytest.raises(ValueError):
            difference_same_run_accumulations(earlier, changed)
