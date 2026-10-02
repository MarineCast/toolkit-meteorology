"""Offline hourly source-grid acquisition from retained decoded bundles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import DEFAULT_CONFIG_PATH
from .product import acquire_hourly_weather


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--date", required=True)
    parser.add_argument("--decoded-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(acquire_hourly_weather(args.config, local_date=args.date,
                                            decoded_dir=args.decoded_dir,
                                            dry_run=args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
