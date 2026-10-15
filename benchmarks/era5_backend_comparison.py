"""Bounded two-day, seven-field CDS/ARCO comparison; no production ARCO backend.

Run cds, then arco in separate processes with the same new output directory.
The compare phase uses retained regional/source evidence with no provider access.
Optional numcodecs is needed for arco only. All times are UTC.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
from pathlib import Path
import threading
import time

import numpy as np
import pandas as pd
import psutil
import pyarrow as pa
import pyarrow.parquet as pq

from meteorology import backfill_weather
from meteorology import backfill_compute as compute
from meteorology.backfill import OUTPUT_SCHEMA
from meteorology.artifacts import checksum_path
from meteorology.backfill_plan import DEFAULT_VARIABLES
from meteorology.era5.resources import Limits
from meteorology.request import digest
from meteorology.request_storage import RequestBudget

from era5_arco import probe, ROOT, VARIABLES

FIRST, LAST = "2024-01-01", "2024-01-02"
BBOX = [-129.7, 45.9, -121.5, 51.5]
FIELDS = dict(zip(VARIABLES[:7], ["t2m", "d2m", "u10", "v10", "msl", "tcc", "tp"]))
UNITS = dict(t2m="K", d2m="K", u10="m s**-1", v10="m s**-1", msl="Pa", tcc="(0 - 1)", tp="m")
KEYS = ["DATE", "SOURCE_LAT", "SOURCE_LON", "METRIC"]


def save(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)


@contextmanager
def measured():
    stop = threading.Event()
    process = psutil.Process()
    result = dict(peak_rss_bytes=process.memory_info().rss)

    def monitor():
        while not stop.wait(0.02):
            result["peak_rss_bytes"] = max(result["peak_rss_bytes"], process.memory_info().rss)

    thread = threading.Thread(target=monitor, daemon=True)
    started = time.perf_counter()
    thread.start()
    try:
        yield result
    finally:
        result["seconds"] = time.perf_counter() - started
        stop.set()
        thread.join()


def cds(root):
    target = root / "cds"
    target.mkdir()
    with measured() as timing:
        result = backfill_weather(
            FIRST,
            LAST,
            BBOX,
            variables=DEFAULT_VARIABLES,
            cache_dir=target / "cache",
            output_dir=target / "output",
            workers=4,
            max_seconds=900,
            max_requests=512,
            transfer_bytes=512 * 1024**2,
        )
    # Query projection is outside pipeline timing and sampled peak RSS.
    pq.write_table(pa.concat_tables(list(result.iter_tables())), target / "daily.parquet")
    save(
        target / "PERFORMANCE.json",
        dict(
            **timing,
            execution=result.metadata["execution"],
            rows=pq.read_metadata(target / "daily.parquet").num_rows,
            cache_bytes=sum(p.stat().st_size for p in (target / "cache").rglob("*") if p.is_file()),
            output_bytes=sum(
                p.stat().st_size for p in (target / "output").rglob("*") if p.is_file()
            ),
            local_cache_initially_empty=True,
            provider_cache_state="UNCONTROLLED",
        ),
    )


def sample_cube(target):
    """Verified coordinate/time alignment; no invented GRIB forecast/revision evidence."""
    times = pd.date_range(FIRST, periods=49, freq="h", tz="UTC")
    origin = pd.Timestamp("1900-01-01", tz="UTC")
    expected = np.array([int((t - origin).total_seconds() / 3600) for t in times])
    arrays, evidence, coordinates = [], {}, None
    for p, (variable, parameter) in enumerate(FIELDS.items()):
        report = json.loads((target / f"{parameter}.json").read_text())
        attributes = report["variables"][variable]["attrs"]
        if attributes.get("units") != UNITS[parameter] or attributes.get("_ARRAY_DIMENSIONS") != [
            "time",
            "latitude",
            "longitude",
        ]:
            raise ValueError("ARCO field units/dimensions differ from the shared reducer's inputs.")
        artifact = target / f"{parameter}.npz"
        if checksum_path(artifact) != report["regional_sample_artifact"]["sha256"]:
            raise ValueError("Regional sample checksum differs.")
        with np.load(artifact, allow_pickle=False) as stored:
            lat, lon = np.meshgrid(stored["latitude_deg"], stored["longitude_deg"], indexing="ij")
            coords = np.column_stack([lat.ravel(), lon.ravel()])
            order = np.lexsort((coords[:, 1], coords[:, 0]))
            coords = coords[order]
            if coordinates is not None and not np.array_equal(coords, coordinates):
                raise ValueError("ARCO field coordinates differ.")
            coordinates = coords
            hours = 49 if parameter == "tp" else 48
            if not np.array_equal(stored["time_hours_since_1900"], expected[:hours]):
                raise ValueError("ARCO field UTC coordinates differ.")
            values = np.full((49, len(coords)), np.nan)
            values[:hours] = stored[variable].reshape(hours, -1)[:, order]
            arrays.append(values)
            receipts = {r["object"]: r for r in report["receipts"]}
            for h in range(hours):
                receipt = receipts[f"{variable}/{expected[h]}.0.0"]
                evidence[f"{p}/{h}"] = dict(
                    parameter=parameter,
                    valid_time_utc=times[h].isoformat(),
                    source_uri=ROOT + "/" + receipt["object"],
                    source_sha256=receipt["sha256"],
                    interval_hypothesis="one hour ending at valid time"
                    if parameter == "tp"
                    else "instantaneous",
                    forecast_init_step_and_expver="UNAVAILABLE_IN_EXAMINED_ARCO_ARRAYS",
                )
    return dict(
        values=np.array(arrays),
        parameters=np.array(list(FIELDS.values())),
        coordinates=coordinates,
        times=np.array([t.value for t in times]),
        metadata=dict(grid_hash=digest(coordinates.tolist()), evidence=evidence),
    )


def arco(root):
    target = root / "arco"
    target.mkdir()
    with measured() as timing:
        started = time.perf_counter()

        def fetch(variable, parameter):
            report = probe(
                FIRST,
                LAST,
                BBOX,
                target / f"{parameter}.json",
                run=True,
                variables=[variable],
                sample_output=target / f"{parameter}.npz",
                include_terminal_hour=parameter == "tp",
                max_requests=70,
                transfer_bytes=(192 if parameter in {"u10", "v10"} else 128) * 1024**2,
                max_seconds=max(1, 900 - (time.perf_counter() - started)),
            )
            print(
                json.dumps(
                    dict(
                        completed_field=parameter,
                        bytes=report["received_bytes"],
                        seconds=report["total_seconds"],
                    )
                ),
                flush=True,
            )
            return report

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetch, v, p) for v, p in FIELDS.items()]
            reports = [future.result() for future in as_completed(futures)]
        acquisition = time.perf_counter() - started
        cube = sample_cube(target)
        lineage = {}
        ids = np.arange(len(cube["coordinates"]))
        spatial = (ids, [None] * len(ids), np.zeros(len(ids)), "native_grid_points_inside_bbox_v1")
        tables = []
        before = time.perf_counter()
        for day in pd.date_range(FIRST, LAST):
            for frame in compute.day_frames(
                cube,
                None,
                day.date().isoformat(),
                dict(variables=DEFAULT_VARIABLES, frequency="daily"),
                spatial,
                lineage,
            ):
                frame["BACKEND"] = "ARCO_EXPERIMENTAL"
                frame["METHOD"] = "benchmark-only-shared-v2-reducer"
                tables.append(
                    pa.Table.from_pandas(frame, schema=OUTPUT_SCHEMA, preserve_index=False)
                )
        aggregation = time.perf_counter() - before
        before = time.perf_counter()
        pq.write_table(pa.concat_tables(tables), target / "daily.parquet", compression="zstd")
        save(target / "LINEAGE.json", lineage)
        writing = time.perf_counter() - before
    save(
        target / "PERFORMANCE.json",
        dict(
            **timing,
            acquisition_seconds=acquisition,
            summed_parallel_decode_crop_seconds=sum(r["decode_crop_seconds"] for r in reports),
            aggregation_seconds=aggregation,
            writing_seconds=writing,
            provider_requests=sum(r["provider_requests"] for r in reports),
            response_bytes=sum(r["received_bytes"] for r in reports),
            regional_sample_bytes=sum(r["regional_sample_artifact"]["bytes"] for r in reports),
            rows=sum(t.num_rows for t in tables),
            output_bytes=(target / "daily.parquet").stat().st_size,
            workers=4,
            transfer_cap_bytes=1024**3,
            request_cap=7 * 70,
            max_seconds=900,
            local_cache_initially_empty=True,
            provider_cache_state="UNCONTROLLED",
            production_backend=False,
            interval_semantics="precipitation same-timestamp ending-hour hypothesis; compare with CDS",
        ),
    )


def differences(expected, actual, *, circular=False):
    left, right = np.asarray(expected, dtype=float), np.asarray(actual, dtype=float)
    both = np.isfinite(left) & np.isfinite(right)
    error = right[both] - left[both]
    if circular:
        error = (error + 180) % 360 - 180
    return dict(
        pairs=len(left),
        finite_pairs=int(both.sum()),
        null_or_nonfinite_support_disagreements=int(
            (np.isfinite(left) != np.isfinite(right)).sum()
        ),
        max_absolute_difference=float(np.abs(error).max()) if len(error) else None,
        rmse=float(np.sqrt(np.mean(error**2))) if len(error) else None,
    )


def compare(root):
    left, right = [
        pq.read_table(root / backend / "daily.parquet").to_pandas() for backend in ("cds", "arco")
    ]
    for frame in (left, right):
        frame["DATE"] = frame.DATE.astype(str)
        if frame.duplicated(KEYS).any():
            raise ValueError("Duplicate daily key.")
    joined = left.merge(
        right,
        on=KEYS,
        how="outer",
        validate="one_to_one",
        indicator=True,
        suffixes=("_cds", "_arco"),
    )
    if not (joined._merge == "both").all():
        raise ValueError("Backend daily coordinate/time/metric supports differ.")
    by_metric = {}
    for metric, rows in joined.groupby("METRIC"):
        stats = differences(rows.VALUE_cds, rows.VALUE_arco, circular="DIRECTION" in metric)
        stats["units"] = rows.UNITS_cds.iloc[0]
        for column in (
            "UNITS",
            "STATUS",
            "VALID_HOURS",
            "INVALID_HOURS",
            "INPUT_MISSING_BITS",
            "INVALID_DERIVATION_BITS",
            "VALUE_STATUS",
        ):
            stats[column + "_disagreements"] = int(
                (rows[column + "_cds"] != rows[column + "_arco"]).sum()
            )
        by_metric[metric] = stats
    # Verify all seven hourly inputs against independently normalized retained CDS GRIB.
    scratch = root / "comparison-scratch"
    budget = RequestBudget(
        Limits(memory_bytes=1024**3), namespace=scratch, run_root=scratch / "run"
    )
    cubes = []
    for ordinal, path in enumerate(sorted((root / "cds" / "cache").rglob("SOURCE.json"))):
        record = json.loads(path.read_text())
        source = record["source"]
        if checksum_path(source["path"]) != source["sha256"]:
            raise ValueError("CDS raw checksum differs.")
        cubes.append(
            compute.read_cube(
                compute.normalize(
                    source, record["selection"], scratch / "run" / f"{ordinal}.npz", budget
                )
            )
        )
    region = sample_cube(root / "arco")
    hourly = {}
    for p, parameter in enumerate(region["parameters"]):
        expected = []
        hours = 49 if parameter == "tp" else 48
        for h in range(hours):
            for cube in cubes:
                positions = np.flatnonzero(cube["times"] == region["times"][h])
                indices = np.flatnonzero(cube["parameters"] == parameter)
                if len(positions) and len(indices):
                    ids, _, _, _ = compute.mapping(cube, BBOX, "native", scratch / "mapping")
                    if not np.array_equal(cube["coordinates"][ids], region["coordinates"]):
                        raise ValueError("CDS and ARCO actual regional coordinates differ.")
                    expected.append(cube["values"][indices[0], positions[0], ids])
                    break
            else:
                raise ValueError("Missing CDS hourly comparison input.")
        hourly[str(parameter)] = differences(
            np.array(expected).ravel(), region["values"][p, :hours].ravel()
        )
    report = dict(
        first=FIRST,
        last=LAST,
        bbox=BBOX,
        emitted_native_points=len(region["coordinates"]),
        source_parameters=list(FIELDS.values()),
        daily_rows_compared=len(joined),
        metrics=by_metric,
        hourly_comparison=hourly,
        performance={
            b: json.loads((root / b / "PERFORMANCE.json").read_text()) for b in ("cds", "arco")
        },
        source_attribution="Copernicus Climate Change Service ERA5 via CDS and Google's public ARCO copy",
        software_compute_sha256=checksum_path(compute.__file__),
        acceptance_threshold="NOT_ADOPTED_DESCRIPTIVE_NUMERICAL_COMPARISON_ONLY",
        independent_regional_accuracy="NOT_RUN",
        production_ARCO_backend=False,
        caveats=[
            "Provider cache/request history uncontrolled; separate fresh local directories/processes.",
            "ARCO shares arithmetic, not CDS init/step/expver provenance or transaction protocol.",
            "One two-day sample cannot establish multi-year throughput or all-era equivalence.",
        ],
    )
    save(root / "COMPARISON.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("cds", "arco", "compare"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date", default=FIRST)
    parser.add_argument("--end-date", default=LAST)
    args = parser.parse_args()
    if (date.fromisoformat(args.end_date) - date.fromisoformat(args.start_date)).days != 1:
        parser.error("The matched benchmark is bounded to exactly two inclusive UTC days.")
    FIRST, LAST = args.start_date, args.end_date
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        dict(cds=cds, arco=arco, compare=compare)[args.phase](args.output_dir)
    except BaseException as error:
        # Retain successful field reports/source state. Never retry an unknown CDS POST.
        failure = args.output_dir / (args.phase + "-FAILURE.json")
        if not failure.exists():
            save(
                failure,
                dict(
                    exception_type=type(error).__name__, utc=datetime.now(timezone.utc).isoformat()
                ),
            )
        raise
