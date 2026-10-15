"""Replay a retained CDS chunk locally, optionally comparing a previous reducer.

No provider access. Reads verified raw/edge evidence, writes bounded normalized
scratch and a report under --output-dir. Timings exclude decoding and publication.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import time

import pandas as pd

from meteorology import backfill_compute as current
from meteorology.artifacts import checksum_path
from meteorology.backfill_plan import DEFAULT_VARIABLES
from meteorology.era5.resources import Limits
from meteorology.request import digest
from meteorology.request_storage import RequestBudget


def replay(args):
    record = json.loads(args.source_record.read_text())
    if record.get("sha256") != digest({k: v for k, v in record.items() if k != "sha256"}):
        raise ValueError("Source record checksum differs.")
    source, selection = record["source"], record["selection"]
    if Path(source["path"]).stat().st_size > 64 * 1024**2:
        raise ValueError("Replay raw source exceeds its 64 MiB cap.")
    if checksum_path(source["path"]) != source["sha256"]:
        raise ValueError("Raw source checksum differs.")
    days = pd.date_range(args.start, args.end, tz="UTC", freq="D")
    available = {f"{selection['year'][0]}-{selection['month'][0]}-{d}" for d in selection["day"]}
    if not len(days) or len(days) > 31 or any(d.date().isoformat() not in available for d in days):
        raise ValueError("Replay must select 1–31 days inside its source chunk.")
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=False)
    budget = RequestBudget(Limits(memory_bytes=1024**3), namespace=root, run_root=root / "run")
    cube = current.read_cube(
        current.normalize(
            source, selection, root / "run" / "month.npz", budget, validated_subset=True
        )
    )
    boundary = current.read_cube(args.boundary_cube) if args.boundary_cube else None
    spatial = current.mapping(cube, args.bbox, "native", root / "mapping")
    modules = {"current": current}
    if args.reference_module:
        spec = importlib.util.spec_from_file_location(
            "meteorology._reference_backfill_compute", args.reference_module
        )
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        modules["reference"] = reference
    timings = dict.fromkeys(modules, 0.0)
    rows = 0
    for ordinal, day in enumerate(days):
        budget.checkpoint()
        results = {}
        order = list(modules)
        if ordinal % 2:
            order.reverse()
        for name in order:
            lineage = {}
            started = time.perf_counter()
            frames = list(
                modules[name].day_frames(
                    cube,
                    boundary,
                    day.date().isoformat(),
                    dict(variables=DEFAULT_VARIABLES, frequency="daily"),
                    spatial,
                    lineage,
                )
            )
            timings[name] += time.perf_counter() - started
            results[name] = frames, lineage
        rows += sum(len(frame) for frame in results["current"][0])
        if "reference" in results:
            if results["reference"][1] != results["current"][1]:
                raise AssertionError("Source lineage differs.")
            for before, after in zip(results["reference"][0], results["current"][0], strict=True):
                pd.testing.assert_frame_equal(before, after, check_exact=True)
    report = dict(
        evidence_kind="retained_source_local_replay",
        start=args.start,
        end=args.end,
        bbox=args.bbox,
        rows=rows,
        seconds=timings,
        speedup=timings["reference"] / timings["current"] if "reference" in timings else None,
        exact_all_column_and_lineage_parity=True if "reference" in timings else None,
        source_sha256=source["sha256"],
        current_compute_sha256=checksum_path(current.__file__),
        reference_compute_sha256=checksum_path(args.reference_module)
        if args.reference_module
        else None,
        provider_requests=0,
        exclusions=[
            "network",
            "normalization",
            "Parquet publication",
            "independent regional accuracy",
        ],
    )
    (root / "PERFORMANCE.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-record", type=Path, required=True)
    parser.add_argument("--boundary-cube", type=Path)
    parser.add_argument("--reference-module", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--bbox", nargs=4, type=float, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    print(json.dumps(replay(parser.parse_args()), indent=2))
