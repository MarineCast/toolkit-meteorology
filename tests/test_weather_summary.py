from datetime import date

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from meteorology.temporal_products import HOURLY_CORE
from meteorology.weather_summary import SCHEMA, _accumulate, _empty, _rows, _validate_table


def test_summary_equal_cell_weights_and_unavailable_are_explicit(tmp_path):
    cells = ["a", "b"]
    rows = []
    for cell, value in (("a", 10), ("b", 30)):
        rows.append(dict(H3_INDEX=cell, AVAILABLE_AT_UTC="2024-01-02T07:00:00Z",
                         **dict.fromkeys(HOURLY_CORE, value)))
    state = _empty(2)
    _accumulate(state, pd.DataFrame(rows), cells)
    result = _rows(state, cells, date(2024, 1, 2), "day", "UTC", "both")
    regional = next(row for row in result if row["SPATIAL_SCOPE"] == "region")
    assert regional["SAMPLED_MEAN"] == 20
    assert regional["SAMPLED_MIN"] == 10
    assert regional["SAMPLED_MAX"] == 30
    assert regional["VALID_CELL_HOURS"] == 2
    assert regional["EXPECTED_CELL_HOURS"] == 48
    empty = _rows(_empty(2), cells, date(2024, 1, 1), "week", "UTC", "region")
    assert all(row["STATUS"] == "UNAVAILABLE" and row["SAMPLED_MEAN"] is None
               and row["AVAILABLE_AT_UTC"] is None for row in empty)


def test_summary_validator_rejects_false_coverage_and_duplicate_keys(tmp_path):
    cells = ["a"]
    state = _empty(1)
    daily = _rows(state, cells, date(2024, 1, 2), "day", "UTC", "both")
    weekly = _rows(state, cells, date(2024, 1, 1), "week", "UTC", "both")
    path = tmp_path / "summary.parquet"
    settings = dict(h3_cells=cells, spatial_scope="both", timezone="UTC", as_of_utc=None)
    coverage = dict(start_date="2024-01-02", end_date="2024-01-02")

    def write(groups):
        with pq.ParquetWriter(path, SCHEMA) as writer:
            for group in groups:
                writer.write_table(pa.Table.from_pylist(group, schema=SCHEMA))

    write([daily, weekly])
    assert _validate_table(path, settings, coverage) == 36
    write([[dict(row, STATUS="COMPLETE") for row in daily], weekly])
    with pytest.raises(ValueError, match="coverage"):
        _validate_table(path, settings, coverage)
    write([daily[:-1] + [daily[0]], weekly])
    with pytest.raises(ValueError, match="duplicate"):
        _validate_table(path, settings, coverage)


def test_parquet_contract_streams_h3_identity_and_excludes_region_null(tmp_path, monkeypatch):
    from meteorology.artifacts import cell_set_hash, parquet_contract

    path = tmp_path / "identities.parquet"
    table = pa.table({"H3_INDEX": ["a", None, "b", "a"], "VALUE": [1, 2, 3, 4]})
    pq.write_table(table, path, row_group_size=2)

    def no_full_read(*args, **kwargs):
        raise AssertionError("Artifact metadata must not materialize the whole H3 column")

    monkeypatch.setattr(pq.ParquetFile, "read", no_full_read)
    contract = parquet_contract(path)
    assert contract["row_count"] == 4
    assert contract["h3_cell_count"] == 2
    assert contract["h3_cell_set_hash"] == cell_set_hash(["a", "b"])
