"""Deterministic synthetic ERA5 processing benchmark; never contacts a provider."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import psutil

from meteorology.artifacts import checksum_path
from meteorology.era5.execution import process_native_day
from meteorology.era5.resources import Budget, Limits
from meteorology.era5.source import normalize_message
from meteorology.era5.planning import DATASET


def main():
    counters = dict(
        decode_calls=0,
        messages=0,
        rows_read=0,
        row_groups_read=0,
        provider_requests=0,
        transferred_bytes=0,
    )
    original = pq.ParquetFile

    class Measured:
        def __init__(self, *args, **kwargs):
            self.file = original(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.file, name)

        def iter_batches(self, *args, **kwargs):
            for batch in self.file.iter_batches(*args, **kwargs):
                counters["rows_read"] += len(batch)
                yield batch

        def read_row_groups(self, groups, *args, **kwargs):
            table = self.file.read_row_groups(groups, *args, **kwargs)
            counters["rows_read"] += len(table)
            counters["row_groups_read"] += len(groups)
            return table

    def decode(path, *, retrieved_at_utc, budget):
        counters["decode_calls"] += 1
        lat = np.repeat(49 + np.arange(16) * 0.25, 16)
        lon = np.tile(-125 + np.arange(16) * 0.25, 16)
        for hour in range(25):
            for param, value in {
                167: 283.15,
                168: 278.15,
                165: 3.0,
                166: 4.0,
                151: 101325.0,
                164: 0.5,
                228: 0.0,
            }.items():
                if hour == 24 and param != 228:
                    continue
                valid = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=hour)
                accum = param == 228
                meta = dict(
                    dataset=DATASET,
                    product_type="reanalysis",
                    centre="ecmf",
                    mars_class="ea",
                    mars_stream="oper",
                    paramId=param,
                    units={
                        167: "K",
                        168: "K",
                        165: "m s**-1",
                        166: "m s**-1",
                        151: "Pa",
                        164: "(0 - 1)",
                        228: "m",
                    }[param],
                    valid_time_utc=valid.isoformat(),
                    retrieved_at_utc=retrieved_at_utc,
                    expver=1,
                    gridType="regular_ll",
                    iDirectionIncrementInDegrees=0.25,
                    jDirectionIncrementInDegrees=0.25,
                    uvRelativeToGrid=0,
                    data_type="fc" if accum else "an",
                    stepType="accum" if accum else "instant",
                    startStep=0,
                    endStep=1 if accum else 0,
                    step_units="hours",
                    init_time_utc=(valid - pd.Timedelta(hours=1) if accum else valid).isoformat(),
                    source_sha256="a" * 64,
                )
                counters["messages"] += 1
                yield normalize_message(meta, lat, lon, np.full(256, value), budget=budget)

    with tempfile.TemporaryDirectory(prefix="meteorology-bench-") as directory:
        root = Path(directory)
        source = root / "synthetic.grib"
        source.write_bytes(b"SYNTHETIC benchmark transport replacement")
        budget = Budget(Limits(memory_bytes=1024**3))
        started = time.perf_counter()
        with (
            patch("meteorology.era5.execution.decode_grib", decode),
            patch("pyarrow.parquet.ParquetFile", Measured),
        ):
            manifest = process_native_day(
                Path(__file__).resolve().parents[1] / "tests/fixtures/study.coastal-policy.v1.json",
                [(source, "2025-01-01T00:00:00Z")],
                root / "processed",
                day="2024-01-01",
                budget=budget,
                native_source_pilot=True,
            )
        result = dict(
            kind="offline_synthetic_not_real_provider",
            points=256,
            wall_seconds=time.perf_counter() - started,
            current_rss_bytes=psutil.Process().memory_info().rss,
            **counters,
            retained_bytes=sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
            budget=budget.receipt(),
            output_rows=original(manifest.parent / "native-daily.parquet").metadata.num_rows,
            daily_sha256=checksum_path(manifest.parent / "native-daily.parquet"),
        )
        print(json.dumps(result, indent=2))
        if len(sys.argv) > 1:
            Path(sys.argv[1]).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
