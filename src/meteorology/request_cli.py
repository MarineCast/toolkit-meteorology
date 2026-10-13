"""CLI counterpart to the shared request API; independent of study/workspace config."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .capabilities import SOURCES, VARIABLES, ALIASES, available_variables


def discovery(arguments):
    parser = argparse.ArgumentParser(description="Source-specific weather request variables.")
    parser.add_argument("--data-source", choices=SOURCES, required=True)
    args = parser.parse_args(arguments)
    print(json.dumps(available_variables(args.data_source), indent=2))
    return 0


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variable", choices=[*VARIABLES, *ALIASES, "weather"])
    parser.add_argument("--variables", nargs="+", choices=[*VARIABLES, *ALIASES])
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--bbox", nargs=4, type=float, required=True, metavar=("WEST", "SOUTH", "EAST", "NORTH")
    )
    parser.add_argument("--data-source", choices=SOURCES, required=True)
    parser.add_argument("--frequency", choices=["daily", "hourly"], default="daily")
    parser.add_argument("--spatial-output", choices=["h3", "native"])
    parser.add_argument("--h3-resolution", type=int, default=5)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--credential-env-file", type=Path)
    parser.add_argument(
        "--source-files",
        type=Path,
        nargs="+",
        help="One-day generated ERA5 GRIB fixtures; synthetic cache only.",
    )
    parser.add_argument("--retrieved-at-utc")
    parser.add_argument("--memory-mib", type=int, default=1024)
    parser.add_argument("--staging-mib", type=int, default=2048)
    parser.add_argument("--transfer-mib", type=int, default=512)
    parser.add_argument("--max-requests", type=int, default=1024)
    parser.add_argument("--max-seconds", type=float, default=3600)
    args = parser.parse_args(arguments)
    if args.variable == "weather" and not args.variables:
        parser.error("get weather requires --variables")
    if args.variable != "weather" and args.variables:
        parser.error("--variables is only used with get weather")
    if not args.dry_run and args.output is None:
        parser.error("execution requires --output")
    from .request import get_weather
    from .era5.resources import Limits

    limits = Limits(
        memory_bytes=args.memory_mib * 1024**2,
        staging_bytes=args.staging_mib * 1024**2,
        transfer_bytes=args.transfer_mib * 1024**2,
        requests=args.max_requests,
        seconds=args.max_seconds,
    )
    result = get_weather(
        args.variables or [args.variable],
        args.start_date,
        args.end_date,
        args.bbox,
        args.data_source,
        frequency=args.frequency,
        spatial_output=args.spatial_output,
        h3_resolution=args.h3_resolution,
        cache_dir=args.cache_dir,
        limits=limits,
        dry_run=args.dry_run,
        refresh=args.refresh,
        source_files=args.source_files,
        retrieved_at_utc=args.retrieved_at_utc,
        credential_env_file=args.credential_env_file,
    )
    if args.dry_run:
        print(json.dumps(result, indent=2))
    else:
        result.to_parquet(args.output)
        print(
            json.dumps(dict(output=str(args.output.resolve()), metadata=result.metadata), indent=2)
        )
    return 0
