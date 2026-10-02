"""Inspect a published hourly family after deep validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from .product import _paths, validate_hourly_product


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    default = _paths(load_meteorological_config(args.config))[3] / "MANIFEST.json"
    print(json.dumps(validate_hourly_product(args.manifest or default), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
