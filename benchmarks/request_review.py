"""Offline review workload; counts are deterministic, timings are descriptive."""

import json
from pathlib import Path
import resource
import psutil
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))
from tests.test_request_transport import hrrr_http_fixture
from meteorology import get_temperature, get_wind
from meteorology.era5.resources import Budget, Limits
import meteorology.hourly_weather.native_day as native_day
import meteorology.hourly_weather.native_source as native_source
from functools import partial


def main():
    measurements = []
    with tempfile.TemporaryDirectory(prefix="request-review-") as folder:
        root = Path(folder)
        opener, bbox = hrrr_http_fixture(root)
        options = dict(cache_dir=root / "cache", spatial_output="native")
        counts = dict(stats=0, traversals=0, decodes=0)
        original_stat, original_glob = Path.stat, Path.rglob
        original_decode = native_source.decode_hour

        def stat(path, *a, **kw):
            counts["stats"] += 1
            return original_stat(path, *a, **kw)

        def glob(path, *a, **kw):
            counts["traversals"] += 1
            return original_glob(path, *a, **kw)

        def decode(*a, **kw):
            counts["decodes"] += 1
            return original_decode(*a, expected_shape=(4, 4), **kw)

        def measure(name, fn):
            counts.update(stats=0, traversals=0, decodes=0)
            before_calls = len(opener.calls)
            start = time.perf_counter()
            error, result = None, None
            with patch.object(Path, "stat", stat), patch.object(Path, "rglob", glob):
                try:
                    result = fn()
                except ValueError as exc:
                    error = str(exc)
            seconds = time.perf_counter() - start
            retained = sum(
                p.stat().st_size for p in (root / "cache/real").rglob("*") if p.is_file()
            )
            temporary = sum(
                p.stat().st_size for p in (root / "cache/real/runs").rglob("*") if p.is_file()
            )
            measurements.append(
                dict(
                    scenario=name,
                    **counts,
                    seconds=seconds,
                    provider_requests=len(opener.calls) - before_calls,
                    error=error,
                    transferred_bytes=(
                        0
                        if result.cache_hit
                        else result.metadata["budget"]["received_transfer_bytes"]
                    )
                    if result
                    else None,
                    intermediate_row_groups=0,
                    retained_namespace_bytes=retained,
                    run_tree_bytes=temporary,
                    temporary_partial_bytes=sum(
                        p.stat().st_size
                        for p in (root / "cache/real/runs").rglob("*.partial")
                        if p.is_file()
                    ),
                    current_rss_bytes=psutil.Process().memory_info().rss,
                    peak_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                )
            )

        with (
            patch.object(native_day, "NativeHTTP", partial(native_day.NativeHTTP, opener=opener)),
            patch.object(native_source, "decode_hour", decode),
        ):
            get_temperature("2024-01-01", "2024-01-01", bbox, "hrrr", frequency="hourly", **options)
            measure(
                "warm-final",
                lambda: get_temperature(
                    "2024-01-01", "2024-01-01", bbox, "hrrr", frequency="hourly", **options
                ),
            )
            measure(
                "warm-source",
                lambda: get_temperature(
                    "2024-01-01",
                    "2024-01-01",
                    bbox,
                    "hrrr",
                    limits=Limits(
                        memory_bytes=1024**3, requests=1 if sys.argv[-1] == "after" else 1024
                    ),
                    **options,
                ),
            )
            measure(
                "partial-source",
                lambda: get_wind(
                    "2024-01-01",
                    "2024-01-01",
                    bbox,
                    "hrrr",
                    limits=Limits(
                        memory_bytes=1024**3, requests=48 if sys.argv[-1] == "after" else 1024
                    ),
                    **options,
                ),
            )
        for entries in (0, 100, 1000):
            storage = root / f"account-{entries}"
            storage.mkdir()
            for i in range(entries):
                (storage / str(i)).write_bytes(b"fixture")
            counts.update(stats=0, traversals=0, decodes=0)
            start = time.perf_counter()
            with patch.object(Path, "stat", stat), patch.object(Path, "rglob", glob):
                if sys.argv[-1] == "after":
                    from meteorology.request_storage import RequestBudget

                    run = storage / "runs/run"
                    run.mkdir(parents=True)
                    budget = RequestBudget(Limits(), namespace=storage, run_root=run)
                else:
                    budget = Budget(Limits(), staging_root=storage)
                for _ in range(100):
                    budget.check_disk(storage, additional_bytes=10)
            measurements.append(
                dict(
                    scenario="storage",
                    entries=entries,
                    **counts,
                    seconds=time.perf_counter() - start,
                )
            )
    print(json.dumps(measurements, indent=2))


if __name__ == "__main__":
    main()
