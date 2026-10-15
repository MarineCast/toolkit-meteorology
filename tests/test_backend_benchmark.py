"""Offline checks for the exploratory benchmark's UTC boundary and comparisons."""

from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


@pytest.mark.parametrize("terminal", [False, True])
def test_arco_probe_includes_exact_terminal_utc_hour_without_shift(tmp_path, monkeypatch, terminal):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "benchmarks"))
    import era5_arco as benchmark

    first = pd.Timestamp("2024-01-01", tz="UTC")
    origin = pd.Timestamp("1900-01-01", tz="UTC")
    initial_hour = int((first - origin).total_seconds() / 3600)
    metadata = {".zattrs": {}}
    for variable in benchmark.VARIABLES:
        metadata[variable + "/.zarray"] = dict(chunks=[1, 2, 2], dtype="<f4", compressor={})
        metadata[variable + "/.zattrs"] = {}
    for coordinate, chunks, dtype in (
        ("latitude", [2], "<f4"),
        ("longitude", [2], "<f4"),
        ("time", [8760], "<i8"),
    ):
        metadata[coordinate + "/.zarray"] = dict(chunks=chunks, dtype=dtype, compressor={})
    metadata["time/.zattrs"] = {"units": "hours since 1900-01-01 00:00:00"}
    observed = []

    def open_chunk(url, timeout):
        relative = url.removeprefix(benchmark.ROOT + "/")
        observed.append(relative)
        if relative == ".zmetadata":
            value = json.dumps(dict(metadata=metadata)).encode()
        elif relative == "latitude/0":
            value = np.array([49.25, 49], dtype="<f4").tobytes()
        elif relative == "longitude/0":
            value = np.array([234.75, 235], dtype="<f4").tobytes()
        elif relative.startswith("time/"):
            number = int(relative.split("/")[1])
            value = np.arange(number * 8760, (number + 1) * 8760, dtype="<i8").tobytes()
        else:
            hour = int(relative.split("/")[1].split(".")[0])
            value = np.full((1, 2, 2), hour - initial_hour, dtype="<f4").tobytes()
        result = BytesIO(value)
        result.headers = {"Content-Length": str(len(value))}
        return result

    monkeypatch.setattr(benchmark, "urlopen", open_chunk)
    monkeypatch.setitem(
        sys.modules,
        "numcodecs",
        SimpleNamespace(get_codec=lambda _: SimpleNamespace(decode=lambda x: x)),
    )
    report = benchmark.probe(
        "2024-01-01",
        "2024-01-02",
        [-125.25, 49, -125, 49.25],
        tmp_path / "report.json",
        run=True,
        variables=["total_precipitation"],
        sample_output=tmp_path / "regional.npz",
        include_terminal_hour=terminal,
    )
    hours = 49 if terminal else 48
    with np.load(tmp_path / "regional.npz") as sample:
        assert sample["total_precipitation"].shape == (hours, 2, 2)
        assert sample["time_hours_since_1900"].tolist() == list(
            range(initial_hour, initial_hour + hours)
        )
        assert sample["total_precipitation"][-1, 0, 0] == hours - 1
    data_chunks = [x for x in observed if x.startswith("total_precipitation/")]
    assert len(data_chunks) == hours
    assert report["include_terminal_hour"] is terminal


def test_backend_comparison_counts_support_disagreements_and_wraps_direction(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "benchmarks"))
    from era5_backend_comparison import differences

    result = differences([359, np.nan, 10, np.inf], [1, 0, np.nan, np.inf], circular=True)
    assert result["pairs"] == 4 and result["finite_pairs"] == 1
    assert result["null_or_nonfinite_support_disagreements"] == 2
    assert result["max_absolute_difference"] == 2 and result["rmse"] == 2
