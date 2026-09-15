from __future__ import annotations

import base64

import numpy as np
import pyarrow as pa
import pytest

from meteorology.surface_weather.time_series_map import (
    _HTML_TEMPLATE,
    WEATHER_METRICS,
    _quantize,
    _validate_and_order_table,
)


def _table() -> pa.Table:
    rows = []
    for date_index, date in enumerate(("2024-01-02", "2024-01-03")):
        for cell_index, cell in enumerate(("85280003fffffff", "85280007fffffff")):
            row = {
                "H3_INDEX": cell,
                "DATE": date,
                "EXPECTED_SAMPLE_COUNT": 6,
                "SAMPLE_COUNT": 6,
                "SAMPLE_COVERAGE_FRAC": 1.0,
                "QC_STATE": "COMPLETE",
            }
            for metric_index, metric in enumerate(WEATHER_METRICS):
                row[metric.field] = float(10 * metric_index + date_index + cell_index / 10)
            rows.append(row)
    return pa.Table.from_pylist([rows[3], rows[0], rows[2], rows[1]])


def test_quantized_metric_round_trip_has_bounded_error() -> None:
    values = np.array([-4.25, 0.0, 11.75, 31.125], dtype=np.float64)
    encoded = _quantize(values)
    raw = np.frombuffer(base64.b64decode(encoded["data"]), dtype="<u2")
    decoded = encoded["offset"] + raw * encoded["scale"]
    assert np.all(np.abs(decoded - values) <= encoded["maximumQuantizationError"] + 1e-12)


def test_time_series_table_is_strictly_validated_and_date_major() -> None:
    ordered, dates, cells = _validate_and_order_table(_table())
    assert dates == ["2024-01-02", "2024-01-03"]
    assert cells == ["85280003fffffff", "85280007fffffff"]
    assert ordered["DATE"].to_pylist() == [
        "2024-01-02",
        "2024-01-02",
        "2024-01-03",
        "2024-01-03",
    ]
    assert ordered["H3_INDEX"].to_pylist() == cells * 2


@pytest.mark.parametrize("defect", ["missing_cell", "nonfinite", "partial_qc"])
def test_time_series_table_fails_closed(defect: str) -> None:
    table = _table()
    if defect == "missing_cell":
        table = table.slice(0, table.num_rows - 1)
    elif defect == "nonfinite":
        index = table.column_names.index(WEATHER_METRICS[0].field)
        table = table.set_column(index, WEATHER_METRICS[0].field, pa.array([np.nan, 1.0, 2.0, 3.0]))
    else:
        index = table.column_names.index("QC_STATE")
        table = table.set_column(
            index, "QC_STATE", pa.array(["COMPLETE", "FAILED", "COMPLETE", "COMPLETE"])
        )
    with pytest.raises(ValueError):
        _validate_and_order_table(table)


def test_html_contract_contains_slider_grid_click_and_series_plot() -> None:
    assert 'id="dateSlider"' in _HTML_TEMPLATE
    assert "layer.on('click', () => selectCell(index))" in _HTML_TEMPLATE
    assert 'id="seriesChart"' in _HTML_TEMPLATE
    assert "event.target.value; scheduleRender(true)" in _HTML_TEMPLATE
    assert "Click the plot to jump to a date" in _HTML_TEMPLATE
