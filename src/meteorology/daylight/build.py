"""Build canonical deterministic daylight and solar-profile products."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

import pandas as pd
import pyarrow as pa

from meteorology.core.artifacts import TransactionalFamilyPublisher
from meteorology.core.data.meteorological_schemas import DAYLIGHT_DOY_SCHEMA, DAYLIGHT_SCHEMA

from ..artifacts import (
    checksum_path,
    manifest_payload,
    parquet_contract,
    write_manifest,
    write_table,
    write_year_partitions,
)
from ..astronomy import solar_profile_metrics
from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from ..spatial_support.build import load_meteorological_support
from .features import (
    build_daylight_day_of_year_features,
    build_daylight_features,
    compact_daylight_weight_output,
)


def _daylight_frame(
    support: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
    timezone: str,
    timestep_minutes: int,
    low_sun_max_degrees: float,
    default_weight: str,
) -> pd.DataFrame:
    cells = support.rename(
        columns={
            "H3_INDEX": "h3",
            "CENTROID_LAT": "centroid_lat",
            "CENTROID_LON": "centroid_lon",
        }
    )[["h3", "centroid_lat", "centroid_lon"]]
    base = build_daylight_features(
        cells,
        start_date,
        end_date,
        default_weight=default_weight,
        validate=True,
    )
    profiles = []
    latitudes = cells["centroid_lat"].to_numpy(dtype=float)
    longitudes = cells["centroid_lon"].to_numpy(dtype=float)
    h3 = cells["h3"].astype(str).to_numpy()
    for date in pd.date_range(start_date, end_date, freq="D"):
        maximum, daylight_mean, low_sun = solar_profile_metrics(
            date=date,
            latitudes=latitudes,
            longitudes=longitudes,
            timezone_name=timezone,
            timestep_minutes=timestep_minutes,
            low_sun_max_degrees=low_sun_max_degrees,
        )
        profiles.append(
            pd.DataFrame(
                {
                    "h3": h3,
                    "date": date.strftime("%Y-%m-%d"),
                    "solar_elevation_max_deg": maximum,
                    "solar_elevation_daylight_mean_deg": daylight_mean,
                    "low_sun_daylight_hours": low_sun,
                }
            )
        )
    merged = base.merge(
        pd.concat(profiles, ignore_index=True),
        on=["h3", "date"],
        how="left",
        validate="one_to_one",
    )
    merged = merged.rename(columns={"h3": "H3_INDEX"})
    merged.columns = [str(column).upper() for column in merged.columns]
    for column in ["YEAR", "DAY_OF_YEAR", "SOLAR_DAY_365"]:
        merged[column] = merged[column].astype("int16")
    for column in ["MONTH", "DAY"]:
        merged[column] = merged[column].astype("int8")
    return merged[DAYLIGHT_SCHEMA.names].sort_values(["DATE", "H3_INDEX"]).reset_index(drop=True)


def _daylight_doy_frame(support: pd.DataFrame, default_weight: str) -> pd.DataFrame:
    cells = support.rename(
        columns={
            "H3_INDEX": "h3",
            "CENTROID_LAT": "centroid_lat",
            "CENTROID_LON": "centroid_lon",
        }
    )[["h3", "centroid_lat", "centroid_lon"]]
    full = build_daylight_day_of_year_features(
        cells,
        default_weight=default_weight,
        max_day_of_year=366,
        validate=True,
    )
    compact = compact_daylight_weight_output(full).rename(
        columns={
            "h3": "H3_INDEX",
            "day_of_year": "DAY_OF_YEAR",
            "weight_daylight": "WEIGHT_DAYLIGHT",
        }
    )
    compact["DAY_OF_YEAR"] = compact["DAY_OF_YEAR"].astype("int16")
    compact["WEIGHT_DAYLIGHT"] = compact["WEIGHT_DAYLIGHT"].astype("float64")
    return compact.sort_values(["DAY_OF_YEAR", "H3_INDEX"]).reset_index(drop=True)


def validate_daylight_product(frame: pd.DataFrame, support: pd.DataFrame) -> None:
    if frame.duplicated(["H3_INDEX", "DATE"]).any() or frame.isna().any().any():
        raise ValueError("Daylight product has duplicate keys or null values.")
    cells = set(support["H3_INDEX"].astype(str))
    for date, group in frame.groupby("DATE"):
        if set(group["H3_INDEX"].astype(str)) != cells:
            raise ValueError(f"Daylight support mismatch for {date}.")
    bounds = {
        "DAYLIGHT_HOURS": (0.0, 24.0),
        "DAYLIGHT_FRACTION": (0.0, 1.0),
        "SOLAR_ELEVATION_MAX_DEG": (-90.0, 90.0),
        "SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG": (0.0, 90.0),
        "LOW_SUN_DAYLIGHT_HOURS": (0.0, 25.0),
    }
    for field, (lower, upper) in bounds.items():
        if not pd.to_numeric(frame[field], errors="coerce").between(lower, upper).all():
            raise ValueError(f"Daylight field {field} is outside [{lower}, {upper}].")


def build_daylight(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    run_id: str | None = None,
) -> tuple[Path, Path, Path]:
    config = load_meteorological_config(config_path)
    daylight = config.daylight
    start = str(start_date or daylight.start_date)
    end = str(end_date or daylight.end_date)
    support = load_meteorological_support(daylight.h3_resolution, config_path)
    frame = _daylight_frame(
        support,
        start_date=start,
        end_date=end,
        timezone=daylight.timezone,
        timestep_minutes=daylight.timestep_minutes,
        low_sun_max_degrees=daylight.low_sun_max_degrees,
        default_weight=daylight.default_weight,
    )
    doy = _daylight_doy_frame(support, daylight.default_weight)
    validate_daylight_product(frame, support)
    run_id = run_id or f"daylight-{uuid.uuid4().hex[:12]}"
    with TransactionalFamilyPublisher(daylight.daily_output_dir.parent, run_id=run_id) as publisher:
        staged_daily = publisher.stage_path(daylight.daily_output_dir)
        staged_doy = publisher.stage_path(daylight.day_of_year_output_path)
        staged_manifest = publisher.stage_path(daylight.manifest_path)
        write_year_partitions(
            pa.Table.from_pandas(frame, preserve_index=False),
            staged_daily,
            DAYLIGHT_SCHEMA,
        )
        write_table(
            staged_doy,
            pa.Table.from_pandas(doy, preserve_index=False),
            DAYLIGHT_DOY_SCHEMA,
        )
        contracts = []
        for staged, final in [
            (staged_daily, daylight.daily_output_dir),
            (staged_doy, daylight.day_of_year_output_path),
        ]:
            contract = parquet_contract(staged, published_path=final)
            contracts.append(contract)
        payload = manifest_payload(
            product="meteorological.daylight",
            run_id=run_id,
            config_path=config.path,
            resolved_config={
                "start_date": start,
                "end_date": end,
                "timezone": daylight.timezone,
                "timestep_minutes": daylight.timestep_minutes,
                "h3_resolution": daylight.h3_resolution,
                "low_sun_max_degrees": daylight.low_sun_max_degrees,
                "default_weight": daylight.default_weight,
                "leap_day_policy": "February 29 uses solar_day_365=60, matching March 1",
            },
            artifacts=contracts,
            inputs=[
                {
                    "path": str(config.support_path(daylight.h3_resolution)),
                    "checksum": checksum_path(config.support_path(daylight.h3_resolution)),
                },
                {
                    "path": str(config.support_manifest_path),
                    "checksum": checksum_path(config.support_manifest_path),
                },
            ],
            sources=[
                {
                    "name": "Dependency-light solar-position approximation",
                    "license": "OrcaCast implementation",
                    "attribution": "OrcaCast",
                    "observation_period": "Not applicable; deterministic astronomy",
                    "redistribution_restrictions": "None",
                }
            ],
            h3_resolution=daylight.h3_resolution,
            spatial_bounds=config.bbox,
            temporal_coverage={"start_date": start, "end_date": end},
            source_completeness="not_applicable_deterministic",
            availability_semantics="Deterministic for any configured local date; no observational publication lag.",
            formulas={
                "DAYLIGHT_HOURS": "(24/pi)*arccos(-tan(latitude)*tan(solar_declination))",
                "LOW_SUN_DAYLIGHT_HOURS": f"local sample hours with geometric solar elevation >0 and <{daylight.low_sun_max_degrees} degrees",
            },
            units={
                "DAYLIGHT_HOURS": "hours",
                "SOLAR_ELEVATION_MAX_DEG": "degrees",
                "SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG": "degrees",
                "LOW_SUN_DAYLIGHT_HOURS": "hours",
            },
            limitations=[
                "Solar position is approximate and ignores atmospheric refraction and local horizon obstruction.",
                "Daylight and solar fields are observer-effort covariates, not occurrence evidence.",
            ],
        )
        write_manifest(staged_manifest, payload)
        publisher.publish()
    return (
        daylight.daily_output_dir,
        daylight.day_of_year_output_path,
        daylight.manifest_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    for path in build_daylight(
        args.config, start_date=args.start_date, end_date=args.end_date, run_id=args.run_id
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
