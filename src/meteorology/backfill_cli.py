"""Simple historical weather CLI; job planning and GRIB internals remain optional diagnostics."""

import argparse
import json
from pathlib import Path
import sys

from .backfill import backfill_weather
from .backfill_plan import DEFAULT_VARIABLES
from .capabilities import available_variables


def main(arguments=None, *, study_config=None):
    parser = argparse.ArgumentParser(
        description="Retrieve resumable historical ERA5 weather in monthly chunks."
    )
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--study-config", type=Path, default=study_config)
    parser.add_argument("--data-source", choices=["era5"], default="era5")
    parser.add_argument(
        "--variables",
        nargs="+",
        choices=[name for name, info in available_variables("era5").items() if info["supported"]],
        default=list(DEFAULT_VARIABLES),
    )
    parser.add_argument("--frequency", choices=["daily", "hourly"], default="daily")
    parser.add_argument("--spatial-output", choices=["native", "h3"], default="native")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--memory-mib", type=int, default=1024)
    parser.add_argument("--staging-mib", type=int, default=2048)
    parser.add_argument("--source-chunk-mib", type=int, default=64)
    parser.add_argument("--transfer-mib", type=int, default=65536)
    parser.add_argument("--retained-cache-mib", type=int)
    parser.add_argument("--max-requests", type=int, default=20000)
    parser.add_argument("--max-seconds", type=float, default=48 * 3600)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retain-intermediates", action="store_true")
    parser.add_argument("--credential-env-file", type=Path)
    args = parser.parse_args(arguments)

    def progress(value):
        print(
            f"Completed {value['completed_days']}/{value['total_days']} UTC days; "
            f"{value['elapsed_seconds']:.1f} seconds",
            file=sys.stderr,
        )

    result = backfill_weather(
        args.start_date,
        args.end_date,
        args.bbox,
        args.data_source,
        variables=args.variables,
        frequency=args.frequency,
        spatial_output=args.spatial_output,
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        workers=args.workers,
        resume=args.resume,
        dry_run=args.dry_run,
        study_config=args.study_config,
        memory_bytes=args.memory_mib * 1024**2,
        staging_bytes=args.staging_mib * 1024**2,
        source_chunk_bytes=args.source_chunk_mib * 1024**2,
        transfer_bytes=args.transfer_mib * 1024**2,
        retained_cache_bytes=None
        if args.retained_cache_mib is None
        else args.retained_cache_mib * 1024**2,
        max_requests=args.max_requests,
        max_seconds=args.max_seconds,
        retries=args.retries,
        retain_intermediates=args.retain_intermediates,
        credential_env_file=args.credential_env_file,
        progress=None if args.dry_run else progress,
    )
    print(json.dumps(result if args.dry_run else result.metadata, indent=2))
    return 0
