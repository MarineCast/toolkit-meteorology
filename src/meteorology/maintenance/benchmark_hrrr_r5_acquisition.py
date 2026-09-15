"""Benchmark legacy grid retention against direct sampled R5 HRRR acquisition."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any, Callable

import pandas as pd
import pyarrow as pa

from meteorology.core.artifacts import atomic_write_json
from meteorology.artifacts import write_table
from meteorology.config import (
    DEFAULT_CONFIG_PATH,
    load_meteorological_config,
)
from meteorology.spatial_support.build import (
    load_meteorological_support,
)
from meteorology.surface_weather.sampling import (
    SAMPLE_SCHEMA,
    build_nearest_grid_crosswalk,
    make_sample_times_for_local_date,
    sample_source_grid,
)
from meteorology.surface_weather.source import (
    RAW_SCHEMA,
    fetch_cropped_hrrr_grid,
)


def _directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*.parquet"))


def _run(
    *,
    label: str,
    times: list[pd.Timestamp],
    workers: int,
    destination: Path,
    operation: Callable[[pd.Timestamp, Path], int],
) -> dict[str, Any]:
    started = time.perf_counter()
    rows = 0
    failures: list[dict[str, str]] = []

    def execute(valid: pd.Timestamp) -> int:
        stamp = valid.tz_convert("UTC").strftime("%Y%m%dT%H%M%SZ")
        return operation(valid, destination / f"{stamp}.parquet")

    if workers == 1:
        for valid in times:
            try:
                rows += execute(valid)
            except Exception as exc:
                failures.append(
                    {"valid_time_utc": valid.isoformat(), "error": f"{type(exc).__name__}: {exc}"}
                )
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(execute, valid): valid for valid in times}
            for future in as_completed(futures):
                try:
                    rows += future.result()
                except Exception as exc:
                    valid = futures[future]
                    failures.append(
                        {
                            "valid_time_utc": valid.isoformat(),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
    elapsed = time.perf_counter() - started
    return {
        "label": label,
        "workers": workers,
        "requests": len(times),
        "successful_requests": len(times) - len(failures),
        "failures": failures,
        "wall_seconds": elapsed,
        "requests_per_minute": (len(times) - len(failures)) / elapsed * 60.0,
        "retained_rows": rows,
        "retained_bytes": _directory_bytes(destination),
    }


def benchmark(
    config_path: str | Path,
    *,
    dates: list[str],
    output_path: str | Path,
) -> dict[str, Any]:
    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    support = load_meteorological_support(weather.h3_resolution, config_path)
    times = [
        valid
        for date in dates
        for valid in make_sample_times_for_local_date(
            date, weather.timezone, weather.interval_hours
        )
    ]

    def fetch(valid: pd.Timestamp) -> pd.DataFrame:
        frame, _ = fetch_cropped_hrrr_grid(
            valid_time_utc=valid,
            bbox=config.bbox,
            bbox_padding_degrees=weather.bbox_padding_degrees,
            availability_lag_hours=weather.availability_lag_hours,
        )
        return frame

    crosswalks: dict[str, pd.DataFrame] = {}
    lock = Lock()

    def retain_grid(valid: pd.Timestamp, path: Path) -> int:
        frame = fetch(valid)
        write_table(path, pa.Table.from_pandas(frame, preserve_index=False), RAW_SCHEMA)
        return len(frame)

    def retain_sample(valid: pd.Timestamp, path: Path) -> int:
        frame = fetch(valid)
        grid_hash = str(frame["SOURCE_GRID_HASH"].iloc[0])
        with lock:
            crosswalk = crosswalks.get(grid_hash)
            if crosswalk is None:
                crosswalk = build_nearest_grid_crosswalk(support, frame)
                crosswalks[grid_hash] = crosswalk
        local_date = valid.tz_convert(weather.timezone).strftime("%Y-%m-%d")
        sampled = sample_source_grid(frame, crosswalk, local_date=local_date, valid_time_utc=valid)
        write_table(path, pa.Table.from_pandas(sampled, preserve_index=False), SAMPLE_SCHEMA)
        return len(sampled)

    with tempfile.TemporaryDirectory(prefix="meteorology_hrrr_benchmark_") as temporary:
        root = Path(temporary)
        baseline = _run(
            label="legacy_grid_retention_one_worker",
            times=times,
            workers=1,
            destination=root / "baseline",
            operation=retain_grid,
        )
        candidate = _run(
            label="direct_r5_sampling_four_workers",
            times=times,
            workers=4,
            destination=root / "candidate",
            operation=retain_sample,
        )
    speedup = baseline["wall_seconds"] / candidate["wall_seconds"]
    row_reduction = (
        baseline["retained_rows"] / candidate["retained_rows"]
        if candidate["retained_rows"]
        else 0.0
    )
    passed = (
        not baseline["failures"]
        and not candidate["failures"]
        and speedup > 1.0
        and row_reduction >= 20.0
    )
    payload = {
        "schema_version": 1,
        "config_path": str(Path(config_path)),
        "dates": dates,
        "valid_times": len(times),
        "baseline": baseline,
        "candidate": candidate,
        "speedup_ratio": speedup,
        "retained_row_reduction_ratio": row_reduction,
        "passed": passed,
        "gate": {
            "zero_failures": True,
            "candidate_faster_than_baseline": True,
            "minimum_retained_row_reduction_ratio": 20.0,
        },
    }
    destination = Path(output_path)
    atomic_write_json(destination, payload, overwrite=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--dates",
        nargs="+",
        default=["2020-12-13", "2023-07-15"],
        help="Representative local dates; latest_complete is appended when absent.",
    )
    parser.add_argument(
        "--output",
        default="outputs/domains/environmental_layer/meteorological/surface_weather/"
        "hrrr_r5_acquisition_benchmark.json",
    )
    args = parser.parse_args()
    config = load_meteorological_config(args.config)
    dates = list(dict.fromkeys([*args.dates, config.surface_weather.resolved_end_date()]))
    payload = benchmark(args.config, dates=dates, output_path=args.output)
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
