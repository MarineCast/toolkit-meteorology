"""Inspect canonical lunar phase and moonlight features."""

from __future__ import annotations

import argparse
from pathlib import Path

from meteorology.core.config.presentation import DEFAULT_PRESENTATION_CONFIG_PATH

from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from ..inspection import available_dates, inspect_daily_product


def inspect_lunar(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    date: str | None = None,
    presentation_config_path: str | Path = DEFAULT_PRESENTATION_CONFIG_PATH,
    output_path: str | Path | None = None,
) -> Path:
    config = load_meteorological_config(config_path)
    product = config.lunar
    dates = available_dates(product.daily_output_dir)
    selected = str(date or dates[-1])
    destination = output_path or (
        Path("outputs/domains/environmental_layer/meteorological/lunar") / "lunar.html"
    )
    return inspect_daily_product(
        dataset_path=product.daily_output_dir,
        manifest_path=product.manifest_path,
        output_path=destination,
        date=selected,
        title="Lunar phase and moonlight exposure",
        metrics=[
            "LUNAR_PHASE_ANGLE_DEG",
            "LUNAR_ILLUMINATION_FRACTION",
            "MOON_VISIBLE_HOURS",
            "MOON_VISIBLE_DARK_HOURS",
            "MOONLIT_DARK_HOURS",
            "MOONLIT_DARK_FRACTION",
        ],
        metadata=["MOON_PHASE_NAME", "NIGHT_HOURS", "CENTROID_LAT", "CENTROID_LON"],
        presentation_config_path=presentation_config_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--date")
    parser.add_argument("--presentation-config", default=DEFAULT_PRESENTATION_CONFIG_PATH)
    parser.add_argument("--output-path")
    args = parser.parse_args()
    print(
        inspect_lunar(
            args.config,
            date=args.date,
            presentation_config_path=args.presentation_config,
            output_path=args.output_path,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
