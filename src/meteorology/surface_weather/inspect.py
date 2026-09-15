"""Inspect canonical daily surface-weather features."""

from __future__ import annotations

import argparse
from pathlib import Path

from meteorology.core.config.presentation import DEFAULT_PRESENTATION_CONFIG_PATH

from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from ..inspection import available_dates, inspect_daily_product


def inspect_surface_weather(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    date: str | None = None,
    presentation_config_path: str | Path = DEFAULT_PRESENTATION_CONFIG_PATH,
    output_path: str | Path | None = None,
) -> Path:
    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    dates = available_dates(weather.daily_output_dir)
    selected = str(date or dates[-1])
    destination = output_path or (
        Path("outputs/domains/environmental_layer/meteorological/surface_weather")
        / "surface_weather.html"
    )
    return inspect_daily_product(
        dataset_path=weather.daily_output_dir,
        manifest_path=weather.manifest_path,
        output_path=destination,
        date=selected,
        title="Surface weather",
        metrics=[
            "TEMPERATURE_2M_C_MEAN",
            "WIND_SPEED_10M_MS_MEAN",
            "PRECIP_MM_DAY_ESTIMATE",
            "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN",
            "VISIBILITY_KM_MIN",
            "SAMPLE_COVERAGE_FRAC",
        ],
        metadata=["QC_STATE", "SAMPLE_COUNT", "EXPECTED_SAMPLE_COUNT", "LATEST_AVAILABLE_AT_UTC"],
        categorical_layers=["QC_STATE"],
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
        inspect_surface_weather(
            args.config,
            date=args.date,
            presentation_config_path=args.presentation_config,
            output_path=args.output_path,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
