from __future__ import annotations

from datetime import timedelta

import pytest

from meteorology.temporal_products import (
    HOURLY_CORE,
    hourly_utc_instants,
    summarize_hourly_window,
    validate_hourly_records,
)


def _hourly(date: str) -> list[dict]:
    result = []
    for valid in hourly_utc_instants(date, "America/Los_Angeles"):
        row = {field: 0.0 for field in HOURLY_CORE}
        row.update(valid_time_utc=valid.isoformat(), init_time_utc=valid.isoformat(),
                   available_at_utc=(valid + timedelta(hours=6)).isoformat(),
                   forecast_hour=0, source_model="HRRR", source_product="sfc")
        result.append(row)
    return result


@pytest.mark.parametrize("date,count", [
    ("2024-03-10", 23), ("2024-02-29", 24), ("2024-11-03", 25),
])
def test_actual_hourly_local_civil_day_has_unique_utc_instants(date: str, count: int) -> None:
    rows = _hourly(date)
    assert len(rows) == count
    assert validate_hourly_records(rows, local_date=date,
                                   timezone_name="America/Los_Angeles")["expected_hours"] == count


def test_hourly_core_rejects_missing_duplicate_and_forecast_substitution() -> None:
    rows = _hourly("2024-01-02")
    for changed in (rows[:-1], rows + [rows[0]]):
        with pytest.raises(ValueError, match="incomplete|Duplicate"):
            validate_hourly_records(changed, local_date="2024-01-02",
                                    timezone_name="America/Los_Angeles")
    rows[0] = dict(rows[0], TEMPERATURE_2M_C=None)
    with pytest.raises(ValueError, match="Required hourly core"):
        validate_hourly_records(rows, local_date="2024-01-02",
                                timezone_name="America/Los_Angeles")
    rows = _hourly("2024-01-02")
    rows[0] = dict(rows[0], forecast_hour=1)
    with pytest.raises(ValueError, match="f00 cycle"):
        validate_hourly_records(rows, local_date="2024-01-02",
                                timezone_name="America/Los_Angeles")


def test_environmental_window_reports_coverage_without_bridging_missing_or_future() -> None:
    rows = _hourly("2024-01-02")[:4]
    start = rows[0]["valid_time_utc"]
    end = hourly_utc_instants("2024-01-02", "America/Los_Angeles")[4].isoformat()
    rows[0]["TEMPERATURE_2M_C"] = 0.0
    rows[1]["TEMPERATURE_2M_C"] = 4.0
    rows[2]["TEMPERATURE_2M_C"] = None
    rows[3]["TEMPERATURE_2M_C"] = 8.0
    result = summarize_hourly_window(rows, start_utc=start, end_utc=end,
                                     field="TEMPERATURE_2M_C")
    assert result["valid_hours"] == 3
    assert result["coverage_fraction"] == 0.75
    assert result["sampled_min"] == 0.0
    assert result["sampled_max"] == 8.0
    assert result["sampled_mean"] == 4.0
    as_of = summarize_hourly_window(rows, start_utc=start, end_utc=end,
                                    field="TEMPERATURE_2M_C", as_of_utc=start)
    assert as_of["valid_hours"] == 0


def test_hourly_h3_inventory_and_optional_coverage() -> None:
    hours = _hourly("2024-03-10")
    rows = [dict(row, H3_INDEX=cell, LOW_CLOUD_COVER_PCT=(0.0 if cell == "a" else None))
            for row in hours for cell in ("a", "b")]
    result = validate_hourly_records(
        rows, local_date="2024-03-10", timezone_name="America/Los_Angeles",
        expected_h3_cells={"a", "b"}, optional_fields=("LOW_CLOUD_COVER_PCT",),
    )
    assert result["expected_rows"] == 46
    assert result["observed_hours"] == 23
    assert result["optional_field_coverage"] == {"LOW_CLOUD_COVER_PCT": 0.5}
    with pytest.raises(ValueError, match="incomplete"):
        validate_hourly_records(rows[:-1], local_date="2024-03-10",
                                timezone_name="America/Los_Angeles",
                                expected_h3_cells={"a", "b"})
    with pytest.raises(ValueError, match="Duplicate"):
        validate_hourly_records(rows + [rows[0]], local_date="2024-03-10",
                                timezone_name="America/Los_Angeles",
                                expected_h3_cells={"a", "b"})
    with pytest.raises(ValueError, match="Unexpected"):
        validate_hourly_records([dict(rows[0], H3_INDEX="c"), *rows[1:]],
                                local_date="2024-03-10", timezone_name="America/Los_Angeles",
                                expected_h3_cells={"a", "b"})
    with pytest.raises(ValueError, match="Select an H3 cell"):
        summarize_hourly_window(rows, start_utc=hours[0]["valid_time_utc"],
                                end_utc=hours[1]["valid_time_utc"], field="TEMPERATURE_2M_C")
    selected = summarize_hourly_window(rows, start_utc=hours[0]["valid_time_utc"],
                                       end_utc=hours[1]["valid_time_utc"],
                                       field="TEMPERATURE_2M_C", h3_index="b")
    assert selected["valid_hours"] == 1
