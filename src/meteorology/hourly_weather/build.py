"""Build the distinct H3 hourly atmosphere family from a pinned acquisition."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config import DEFAULT_CONFIG_PATH
from .product import build_hourly_weather


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--acquisition-manifest", type=Path)
    args = parser.parse_args()
    print(build_hourly_weather(args.config, acquisition_manifest=args.acquisition_manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
