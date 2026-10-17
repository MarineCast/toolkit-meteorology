"""Compare existing daily acquisition with historical batching, preserving numeric parity.

Default uses generated GRIB/mock transport. --live explicitly uses configured CDS
credentials and manually accepted terms. No synthetic result is provider validation.
"""

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import numpy as np
import psutil

sys.path.append(str(Path(__file__).resolve().parents[1]))
from meteorology import get_weather_request as get_weather, backfill_weather
from meteorology.backfill_plan import DEFAULT_VARIABLES
from tests.test_backfill import Provider
import meteorology.era5.source as source

PERIODS = {
    "two-day": ("2024-01-01", "2024-01-02"),
    "month": ("2024-01-01", "2024-01-31"),
    "three-month": ("2024-01-01", "2024-03-31"),
}
ALIASES = {
    "TEMPERATURE_2M_C": "TEMPERATURE_2M_C_MEAN",
    "U_WIND_10M_MS": "U_WIND_10M_MS_MEAN",
    "V_WIND_10M_MS": "V_WIND_10M_MS_MEAN",
    "WIND_SPEED_10M_MS": "WIND_SPEED_10M_MS_MEAN",
    "RELATIVE_HUMIDITY_2M_PCT": "RELATIVE_HUMIDITY_2M_PCT_MEAN",
    "TOTAL_CLOUD_COVER_PCT": "TOTAL_CLOUD_COVER_PCT_MEAN",
    "MEAN_SEA_LEVEL_PRESSURE_HPA": "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN",
    "DEWPOINT_2M_C": "DEWPOINT_2M_C_MEAN",
}


def benchmark(period, bbox, directory, *, live=False, workers=2, only=None, baseline_exclude=()):
    first, last = PERIODS[period]
    if set(baseline_exclude) - set(DEFAULT_VARIABLES):
        raise ValueError("Baseline exclusions must name primary benchmark variables.")
    baseline_variables = [v for v in DEFAULT_VARIABLES if v not in baseline_exclude]
    if not baseline_variables:
        raise ValueError("At least one baseline variable is required.")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    provider = None if live else Provider(root)
    decoder = source.decode_grib
    counts = dict(decodes=0, traversals=0)
    rglob = Path.rglob

    def counted_decode(*args, **kwargs):
        counts["decodes"] += 1
        yield from decoder(*args, **kwargs)

    def counted_walk(path, *args, **kwargs):
        counts["traversals"] += 1
        return rglob(path, *args, **kwargs)

    import meteorology.backfill_compute as compute

    contexts = [
        patch.object(source, "decode_grib", counted_decode),
        patch.object(compute, "decode_grib", counted_decode),
        patch.object(Path, "rglob", counted_walk),
    ]
    if provider:
        contexts.extend(
            [
                patch("meteorology.era5.transport.CDSHTTPProvider", provider.factory),
                patch.dict("os.environ", {"CDSAPI_KEY": "SYNTHETIC_TOKEN"}),
            ]
        )
    from contextlib import ExitStack

    measurements = {}
    values = {}
    with ExitStack() as stack:
        for context in contexts:
            stack.enter_context(context)
        for mode in [only] if only else ("baseline", "optimized"):
            counts.update(decodes=0, traversals=0)
            calls = len(provider.calls) if provider else 0
            jobs = set(provider.jobs) if provider else set()
            started = time.perf_counter()
            stopped = threading.Event()
            peak = [psutil.Process().memory_info().rss]

            def monitor():
                while not stopped.wait(0.02):
                    peak[0] = max(peak[0], psutil.Process().memory_info().rss)

            thread = threading.Thread(target=monitor, daemon=True)
            thread.start()
            stack.callback(thread.join)
            stack.callback(stopped.set)
            if mode == "baseline":
                results = []
                cursor = date.fromisoformat(first)
                while cursor <= date.fromisoformat(last):
                    end = min(cursor + timedelta(days=6), date.fromisoformat(last))
                    result = get_weather(
                        baseline_variables,
                        cursor.isoformat(),
                        end.isoformat(),
                        bbox,
                        "era5",
                        cache_dir=root / mode,
                        spatial_output="native",
                        frequency="daily",
                    )
                    results.append(result)
                    cursor = end + timedelta(days=1)
                tables = (table for result in results for table in result._tables())
            else:
                result = backfill_weather(
                    first,
                    last,
                    bbox,
                    variables=DEFAULT_VARIABLES,
                    cache_dir=root / mode,
                    output_dir=root / "outputs",
                    workers=workers,
                    _provider_factory=provider.factory if provider else None,
                )
                tables = result.iter_tables()
            pipeline_seconds = time.perf_counter() - started
            stopped.set()
            thread.join()
            data = {}
            for table in tables:
                for row in (
                    table.select(["DATE", "SOURCE_GRID_INDEX", "METRIC", "VALUE"])
                    .to_pandas()
                    .itertuples(index=False)
                ):
                    metric = (
                        ALIASES.get(row.METRIC, row.METRIC) if mode == "baseline" else row.METRIC
                    )
                    data[(row.DATE, row.SOURCE_GRID_INDEX, metric)] = row.VALUE
            elapsed = time.perf_counter() - started
            values[mode] = data
            measurements[mode] = dict(
                seconds=elapsed,
                pipeline_seconds=pipeline_seconds,
                verification_seconds=elapsed - pipeline_seconds,
                pipeline_peak_rss_bytes=peak[0],
                current_execution=result.metadata.get("execution") if mode == "optimized" else None,
                **counts,
                rss_bytes=psutil.Process().memory_info().rss,
                provider_calls=len(provider.calls) - calls if provider else None,
                source_jobs=len(set(provider.jobs) - jobs) if provider else None,
                payload_bytes=sum(
                    provider.jobs[j].stat().st_size for j in set(provider.jobs) - jobs
                )
                if provider
                else None,
                retained_bytes=sum(
                    p.stat().st_size for p in (root / mode).rglob("*") if p.is_file()
                ),
                output_bytes=sum(
                    p.stat().st_size for p in (root / "outputs").rglob("*") if p.is_file()
                )
                if mode == "optimized"
                else 0,
                rows=len(data),
            )
        paired = "baseline" in values and "optimized" in values
        if paired:
            assert values["baseline"].keys() <= values["optimized"].keys()
        maximum = 0.0
        for key, expected in values["baseline"].items() if paired else []:
            actual = values["optimized"][key]
            if np.isnan(expected):
                assert np.isnan(actual), key
            else:
                assert np.isclose(expected, actual, rtol=1e-10, atol=1e-8), (key, expected, actual)
                maximum = max(maximum, abs(expected - actual))
        report = dict(
            period=period,
            bbox=bbox,
            optimized_variables=list(DEFAULT_VARIABLES),
            baseline_excluded_variables=list(baseline_exclude),
            evidence_kind="real_cds" if live else "generated_grib_mock_transport",
            measurements=measurements,
            numerical_parity=dict(
                compared_values=len(values["baseline"]) if paired else 0,
                max_absolute_difference=maximum if paired else None,
            ),
            baseline_revision="daily strategy with shallow-attrs CPU fix; original full-area reduction interrupted after >20 minutes",
            rss_semantics="20 ms process RSS sampling during pipeline only; use --only in a fresh process to avoid allocator carry-over",
            independent_regional_accuracy="NOT_RUN",
        )
        target = root / "PERFORMANCE.json"
        if target.exists():
            raise FileExistsError("Performance evidence is never overwritten.")
        target.write_text(json.dumps(report, indent=2))
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", choices=PERIODS, default="two-day")
    parser.add_argument("--bbox", nargs=4, type=float, default=[-129.7, 45.9, -121.5, 51.5])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--only", choices=["baseline", "optimized"])
    parser.add_argument("--baseline-exclude", nargs="+", choices=DEFAULT_VARIABLES, default=[])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="meteorology-backfill-benchmark-") as temporary:
        print(
            json.dumps(
                benchmark(
                    args.period,
                    args.bbox,
                    args.output_dir or temporary,
                    live=args.live,
                    workers=args.workers,
                    only=args.only,
                    baseline_exclude=args.baseline_exclude,
                ),
                indent=2,
            )
        )
