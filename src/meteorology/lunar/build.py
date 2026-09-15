"""Build the canonical partitioned R5 lunar product."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

import pandas as pd
import pyarrow as pa

from meteorology.core.artifacts import TransactionalFamilyPublisher
from meteorology.core.data.meteorological_schemas import LUNAR_SCHEMA

from ..artifacts import (
    checksum_path,
    manifest_payload,
    parquet_contract,
    write_manifest,
    write_table,
)
from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from ..spatial_support.build import load_meteorological_support
from .compute import compute_lunar_illumination_table
from .validation import validate_lunar_illumination_features


def _normalize_lunar(frame: pd.DataFrame) -> pd.DataFrame:
    validate_lunar_illumination_features(frame, strict=True)
    out = frame.rename(columns={"h3": "H3_INDEX"})
    out.columns = [str(column).upper() for column in out.columns]
    for field in ["YEAR", "DAY_OF_YEAR"]:
        out[field] = out[field].astype("int16")
    for field in ["MONTH", "DAY"]:
        out[field] = out[field].astype("int8")
    return out[LUNAR_SCHEMA.names].sort_values(["DATE", "H3_INDEX"]).reset_index(drop=True)


def _month_windows(start_date: str, end_date: str):
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("Lunar end_date must be on or after start_date.")
    cursor = start
    while cursor <= end:
        month_end = min(cursor + pd.offsets.MonthEnd(0), end)
        yield cursor.strftime("%Y-%m-%d"), month_end.strftime("%Y-%m-%d")
        cursor = month_end + pd.Timedelta(days=1)


def validate_lunar_support(frame: pd.DataFrame, support: pd.DataFrame) -> None:
    if frame.duplicated(["H3_INDEX", "DATE"]).any():
        raise ValueError("Lunar product contains duplicate H3/date keys.")
    cells = set(support["H3_INDEX"].astype(str))
    for date, group in frame.groupby("DATE"):
        if set(group["H3_INDEX"].astype(str)) != cells:
            raise ValueError(f"Lunar support mismatch for {date}.")


def build_lunar(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    run_id: str | None = None,
) -> tuple[Path, Path]:
    config = load_meteorological_config(config_path)
    lunar = config.lunar
    start = str(start_date or lunar.start_date)
    end = str(end_date or lunar.end_date)
    support = load_meteorological_support(lunar.h3_resolution, config_path)
    cells = support.rename(
        columns={
            "H3_INDEX": "h3",
            "CENTROID_LAT": "centroid_lat",
            "CENTROID_LON": "centroid_lon",
        }
    )[["h3", "centroid_lat", "centroid_lon"]]
    run_id = run_id or f"lunar-{uuid.uuid4().hex[:12]}"
    with TransactionalFamilyPublisher(lunar.daily_output_dir.parent, run_id=run_id) as publisher:
        staged_daily = publisher.stage_path(lunar.daily_output_dir)
        staged_manifest = publisher.stage_path(lunar.manifest_path)
        part_index: dict[str, int] = {}
        date_count = 0
        for window_start, window_end in _month_windows(start, end):
            raw = compute_lunar_illumination_table(
                cells,
                start_date=window_start,
                end_date=window_end,
                sample_hour_utc=lunar.sample_hour_utc,
                timezone_name=lunar.timezone,
                timestep_minutes=lunar.timestep_minutes,
                dark_sun_altitude_deg=lunar.dark_sun_altitude_deg,
                moon_altitude_min_deg=lunar.moon_altitude_min_deg,
            )
            frame = _normalize_lunar(raw)
            validate_lunar_support(frame, support)
            year = window_start[:4]
            index = part_index.get(year, 0)
            write_table(
                staged_daily / f"year={year}" / f"part-{index:03d}.parquet",
                pa.Table.from_pandas(frame, preserve_index=False),
                LUNAR_SCHEMA,
            )
            part_index[year] = index + 1
            date_count += int(frame["DATE"].nunique())
        expected_dates = len(pd.date_range(start, end, freq="D"))
        if date_count != expected_dates:
            raise ValueError(
                f"Lunar build date mismatch: expected {expected_dates}, observed {date_count}."
            )
        contract = parquet_contract(staged_daily, published_path=lunar.daily_output_dir)
        payload = manifest_payload(
            product="meteorological.lunar",
            run_id=run_id,
            config_path=config.path,
            resolved_config={
                "start_date": start,
                "end_date": end,
                "timezone": lunar.timezone,
                "timestep_minutes": lunar.timestep_minutes,
                "sample_hour_utc": lunar.sample_hour_utc,
                "dark_sun_altitude_deg": lunar.dark_sun_altitude_deg,
                "moon_altitude_min_deg": lunar.moon_altitude_min_deg,
                "h3_resolution": lunar.h3_resolution,
            },
            artifacts=[contract],
            inputs=[
                {
                    "path": str(config.support_path(lunar.h3_resolution)),
                    "checksum": checksum_path(config.support_path(lunar.h3_resolution)),
                },
                {
                    "path": str(config.support_manifest_path),
                    "checksum": checksum_path(config.support_manifest_path),
                },
            ],
            sources=[
                {
                    "name": "Dependency-light approximate lunar astronomy",
                    "license": "OrcaCast implementation",
                    "attribution": "OrcaCast",
                    "observation_period": "Not applicable; deterministic astronomy",
                    "redistribution_restrictions": "None",
                }
            ],
            h3_resolution=lunar.h3_resolution,
            spatial_bounds=config.bbox,
            temporal_coverage={"start_date": start, "end_date": end},
            source_completeness="not_applicable_deterministic",
            availability_semantics="Deterministic for any configured local date; no observational publication lag.",
            formulas={
                "MOONLIT_DARK_HOURS": "sum(timestep_hours * lunar_illumination_fraction) when moon is above horizon during darkness",
                "MOONLIT_DARK_FRACTION": "MOONLIT_DARK_HOURS / NIGHT_HOURS",
            },
            units={
                "LUNAR_AGE_DAYS": "days",
                "LUNAR_PHASE_ANGLE_DEG": "degrees",
                "hour_fields": "hours",
                "fraction_fields": "unitless [0,1]",
            },
            limitations=[
                "Solar and lunar positions are dependency-light approximations, not ephemeris-grade astronomy.",
                "Moonlight fields ignore clouds, terrain horizons, sea state, and artificial light.",
            ],
        )
        write_manifest(staged_manifest, payload)
        publisher.publish()
    return lunar.daily_output_dir, lunar.manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    for path in build_lunar(
        args.config, start_date=args.start_date, end_date=args.end_date, run_id=args.run_id
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
