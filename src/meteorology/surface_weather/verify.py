"""Verify the strict direct-HRRR R5 rebuild before legacy migration."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from meteorology.core.artifacts import atomic_write_json

from ..artifacts import load_manifest, sha256_file
from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from ..spatial_support.build import load_meteorological_support
from .build import DAILY_SCHEMA
from .download import INVENTORY_SCHEMA, _expected_times
from .source import hrrr_aws_archive_uri

LEGACY_DEFAULT = Path(
    "data/processed/domain/environmental_layer/meteorological/surface_weather/hrrr"
)

SHARED_LEGACY_FIELDS = {
    "TEMPERATURE_2M_C_MEAN": "temperature_2m_c_mean",
    "RELATIVE_HUMIDITY_2M_PCT_MEAN": "relative_humidity_2m_pct_mean",
    "WIND_SPEED_10M_MS_MEAN": "wind_speed_10m_ms_mean",
    "WIND_SPEED_10M_MS_MAX": "wind_speed_10m_ms_max",
    "WIND_GUST_SURFACE_MS_MEAN": "wind_gust_surface_ms_mean",
    "WIND_GUST_SURFACE_MS_MAX": "wind_gust_surface_ms_max",
    "VISIBILITY_KM_MEAN": "visibility_km_mean",
    "VISIBILITY_KM_MIN": "visibility_km_min",
    "TOTAL_CLOUD_COVER_PCT_MEAN": "total_cloud_cover_pct_mean",
}

ABSOLUTE_TOLERANCES = {
    "TEMPERATURE_2M_C_MEAN": 1e-3,
    "RELATIVE_HUMIDITY_2M_PCT_MEAN": 1e-3,
    "WIND_SPEED_10M_MS_MEAN": 1e-3,
    "WIND_SPEED_10M_MS_MAX": 1e-3,
    "WIND_GUST_SURFACE_MS_MEAN": 1e-3,
    "WIND_GUST_SURFACE_MS_MAX": 1e-3,
    "VISIBILITY_KM_MEAN": 1e-3,
    "VISIBILITY_KM_MIN": 1e-3,
    "TOTAL_CLOUD_COVER_PCT_MEAN": 1e-3,
}

LEGACY_SAMPLE_GROUPS = {
    "temperature": {
        "sample": "TEMPERATURE_2M_C",
        "metrics": {"TEMPERATURE_2M_C_MEAN": "mean"},
    },
    "relative_humidity": {
        "sample": "RELATIVE_HUMIDITY_2M_PCT",
        "metrics": {"RELATIVE_HUMIDITY_2M_PCT_MEAN": "mean"},
    },
    "wind_speed": {
        "sample": "WIND_SPEED_10M_MS",
        "metrics": {
            "WIND_SPEED_10M_MS_MEAN": "mean",
            "WIND_SPEED_10M_MS_MAX": "max",
        },
    },
    "wind_gust": {
        "sample": "WIND_GUST_SURFACE_MS",
        "metrics": {
            "WIND_GUST_SURFACE_MS_MEAN": "mean",
            "WIND_GUST_SURFACE_MS_MAX": "max",
        },
    },
    "visibility": {
        "sample": "VISIBILITY_KM",
        "metrics": {
            "VISIBILITY_KM_MEAN": "mean",
            "VISIBILITY_KM_MIN": "min",
        },
    },
    "cloud_cover": {
        "sample": "TOTAL_CLOUD_COVER_PCT",
        "metrics": {"TOTAL_CLOUD_COVER_PCT_MEAN": "mean"},
    },
}


def verification_report_path(config_path: str | Path = DEFAULT_CONFIG_PATH) -> Path:
    config = load_meteorological_config(config_path)
    return config.surface_weather.daily_output_dir.parent / "R5_REBUILD_VERIFICATION.json"


def _date_from_path(path: Path) -> str | None:
    for part in path.parts:
        if part.startswith("date="):
            return part.removeprefix("date=").removesuffix(".parquet")
    return None


def _legacy_by_date(root: Path) -> dict[str, list[pd.DataFrame]]:
    required = {"h3", "date", "n_obs", *SHARED_LEGACY_FIELDS.values()}
    grouped: dict[str, list[pd.DataFrame]] = defaultdict(list)
    for path in sorted(root.rglob("*.parquet")):
        parquet = pq.ParquetFile(path)
        if not required.issubset(parquet.schema_arrow.names):
            continue
        frame = parquet.read(columns=sorted(required)).to_pandas()
        if frame.empty:
            continue
        for date, date_frame in frame.groupby(frame["date"].astype(str), sort=False):
            grouped[str(date)].append(date_frame.reset_index(drop=True))
    return dict(grouped)


def _reduce_samples(values: np.ndarray, reducer: str, interval_hours: int) -> np.ndarray:
    if reducer == "mean":
        return values.mean(axis=0)
    if reducer == "max":
        return values.max(axis=0)
    if reducer == "min":
        return values.min(axis=0)
    if reducer == "precip_sum":
        return values.sum(axis=0) * float(interval_hours)
    raise ValueError(f"Unsupported legacy comparison reducer: {reducer}")


def _legacy_field_partial_evidence(
    *,
    old: pd.DataFrame,
    sample_files: list[Path],
    interval_hours: int,
) -> dict[str, Any] | None:
    """Prove legacy field omissions, including explicit legacy zero-fill behavior."""

    if len(sample_files) != 6:
        return None
    cells = old["h3"].astype(str).tolist()
    sample_frames: list[pd.DataFrame] = []
    valid_times: list[str] = []
    required_sample_fields = {
        "H3_INDEX",
        "VALID_TIME_UTC",
        *(str(spec["sample"]) for spec in LEGACY_SAMPLE_GROUPS.values()),
    }
    for path in sorted(sample_files):
        parquet = pq.ParquetFile(path)
        if not required_sample_fields.issubset(parquet.schema_arrow.names):
            return None
        frame = parquet.read(columns=sorted(required_sample_fields)).to_pandas()
        if (
            len(frame) != len(cells)
            or frame["H3_INDEX"].duplicated().any()
            or set(frame["H3_INDEX"].astype(str)) != set(cells)
        ):
            return None
        frame = frame.assign(H3_INDEX=frame["H3_INDEX"].astype(str)).set_index("H3_INDEX")
        sample_frames.append(frame.reindex(cells))
        times = frame["VALID_TIME_UTC"].astype(str).unique().tolist()
        if len(times) != 1:
            return None
        valid_times.append(times[0])

    evidence: dict[str, Any] = {}
    for group, spec in LEGACY_SAMPLE_GROUPS.items():
        sample_column = str(spec["sample"])
        values = np.stack(
            [pd.to_numeric(frame[sample_column], errors="coerce") for frame in sample_frames]
        ).astype(float)
        if not np.isfinite(values).all():
            return None
        metrics = dict(spec["metrics"])
        legacy_by_metric = {
            current: pd.to_numeric(old[SHARED_LEGACY_FIELDS[current]], errors="coerce").to_numpy(
                dtype=float
            )
            for current in metrics
        }
        if not all(np.isfinite(values).all() for values in legacy_by_metric.values()):
            return None

        def subset_matches(indices: tuple[int, ...], *, zero_fill: bool) -> bool:
            if zero_fill:
                subset = np.zeros_like(values)
                subset[list(indices), :] = values[list(indices), :]
            else:
                subset = values[list(indices), :]
            for current, reducer in metrics.items():
                calculated = _reduce_samples(subset, reducer, interval_hours)
                if (
                    np.max(np.abs(legacy_by_metric[current] - calculated))
                    > ABSOLUTE_TOLERANCES[current]
                ):
                    return False
            return True

        def cell_subset_matches(
            cell_index: int, indices: tuple[int, ...], *, zero_fill: bool
        ) -> bool:
            if zero_fill:
                subset = np.zeros((6, 1), dtype=float)
                subset[list(indices), 0] = values[list(indices), cell_index]
            else:
                subset = values[list(indices), cell_index : cell_index + 1]
            return all(
                abs(
                    legacy_by_metric[current][cell_index]
                    - _reduce_samples(subset, reducer, interval_hours)[0]
                )
                <= ABSOLUTE_TOLERANCES[current]
                for current, reducer in metrics.items()
            )

        all_indices = tuple(range(6))
        if subset_matches(all_indices, zero_fill=False):
            continue
        matching_subset: tuple[int, ...] | None = None
        behavior = ""
        for zero_fill in (False, True):
            for size in range(5, 0, -1):
                for indices in combinations(all_indices, size):
                    if subset_matches(indices, zero_fill=zero_fill):
                        matching_subset = indices
                        behavior = "zero_filled_omission" if zero_fill else "skipped_omission"
                        break
                if matching_subset is not None:
                    break
            if matching_subset is not None:
                break
        if matching_subset is not None:
            retained = set(matching_subset)
            evidence[group] = {
                "observed_sample_count": len(matching_subset),
                "legacy_missingness_behavior": behavior,
                "omitted_valid_times": [
                    value for index, value in enumerate(valid_times) if index not in retained
                ],
            }
            continue

        affected_cells = 0
        unexplained_cells = 0
        minimum_count = 6
        behavior_counts: dict[str, int] = defaultdict(int)
        omitted_counts: dict[str, int] = defaultdict(int)
        for cell_index in range(values.shape[1]):
            if cell_subset_matches(cell_index, all_indices, zero_fill=False):
                continue
            cell_subset: tuple[int, ...] | None = None
            cell_behavior = ""
            for zero_fill in (False, True):
                for size in range(5, 0, -1):
                    for indices in combinations(all_indices, size):
                        if cell_subset_matches(cell_index, indices, zero_fill=zero_fill):
                            cell_subset = indices
                            cell_behavior = (
                                "zero_filled_omission" if zero_fill else "skipped_omission"
                            )
                            break
                    if cell_subset is not None:
                        break
                if cell_subset is not None:
                    break
            if cell_subset is None:
                if group == "cloud_cover":
                    unexplained_cells += 1
                    continue
                return None
            affected_cells += 1
            minimum_count = min(minimum_count, len(cell_subset))
            behavior_counts[cell_behavior] += 1
            retained = set(cell_subset)
            for index, value in enumerate(valid_times):
                if index not in retained:
                    omitted_counts[value] += 1
        if unexplained_cells:
            full_values = {
                current: _reduce_samples(values, reducer, interval_hours)
                for current, reducer in metrics.items()
            }
            evidence[group] = {
                "legacy_selector_state": "AMBIGUOUS_TCDC_LEVEL",
                "legacy_selector": ":TCDC:",
                "required_selector": ":TCDC:entire atmosphere:",
                "unexplained_selector_contaminated_cell_count": unexplained_cells,
                "subset_explained_cell_count": affected_cells,
                "maximum_absolute_difference_from_required_level": max(
                    float(np.max(np.abs(legacy_by_metric[current] - full_values[current])))
                    for current in metrics
                ),
            }
            continue
        if not affected_cells:
            return None
        evidence[group] = {
            "spatially_variable": True,
            "affected_cell_count": affected_cells,
            "minimum_observed_sample_count": minimum_count,
            "legacy_missingness_behavior_cell_counts": dict(sorted(behavior_counts.items())),
            "omitted_valid_time_cell_counts": dict(sorted(omitted_counts.items())),
        }
    return evidence or None


def _compare_legacy(
    *,
    legacy_root: Path,
    daily_files: dict[str, Path],
    support_cells: set[str],
    sample_files_by_date: dict[str, list[Path]],
    interval_hours: int,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    legacy_available = legacy_root.exists()
    legacy = _legacy_by_date(legacy_root)
    maxima = {column: 0.0 for column in SHARED_LEGACY_FIELDS}
    compared_dates = 0
    compared_rows = 0
    excluded_partial_dates = 0
    excluded_field_partial_dates = 0
    excluded_selector_ambiguous_dates = 0
    field_partial_evidence: dict[str, Any] = {}
    for date, frames in sorted(legacy.items()):
        if date not in daily_files:
            continue
        old = pd.concat(frames, ignore_index=True).drop_duplicates(ignore_index=True)
        complete = (
            not old["h3"].duplicated().any()
            and set(old["h3"].astype(str)) == support_cells
            and set(pd.to_numeric(old["n_obs"], errors="coerce")) == {6}
        )
        if not complete:
            excluded_partial_dates += 1
            continue
        partial_evidence = _legacy_field_partial_evidence(
            old=old,
            sample_files=sample_files_by_date.get(date, []),
            interval_hours=interval_hours,
        )
        if partial_evidence is not None:
            excluded_partial_dates += 1
            if any(
                "legacy_selector_state" in group_evidence
                for group_evidence in partial_evidence.values()
            ):
                excluded_selector_ambiguous_dates += 1
            if any(
                "legacy_missingness_behavior" in group_evidence
                or "affected_cell_count" in group_evidence
                or int(group_evidence.get("subset_explained_cell_count", 0)) > 0
                for group_evidence in partial_evidence.values()
            ):
                excluded_field_partial_dates += 1
            field_partial_evidence[date] = partial_evidence
            continue
        new = pq.ParquetFile(daily_files[date]).read().to_pandas()
        joined = old.merge(
            new,
            left_on=["h3", "date"],
            right_on=["H3_INDEX", "DATE"],
            how="inner",
            validate="one_to_one",
        )
        if len(joined) != len(support_cells):
            errors.append(f"Legacy comparison support mismatch for {date}.")
            continue
        for current, former in SHARED_LEGACY_FIELDS.items():
            difference = np.abs(
                pd.to_numeric(joined[current], errors="coerce").to_numpy(dtype=float)
                - pd.to_numeric(joined[former], errors="coerce").to_numpy(dtype=float)
            )
            maximum = float(np.nanmax(difference))
            maxima[current] = max(maxima[current], maximum)
            if not np.isfinite(difference).all() or maximum > ABSOLUTE_TOLERANCES[current]:
                errors.append(
                    f"Legacy comparison failed for {current} on {date}: "
                    f"maximum absolute difference {maximum}."
                )
        compared_dates += 1
        compared_rows += len(joined)
    if legacy_available and not compared_dates:
        errors.append("No complete legacy dates were available for shared-core comparison.")
    return (
        {
            "legacy_root": str(legacy_root),
            "comparison_status": (
                "complete"
                if compared_dates
                else "no_complete_dates" if legacy_available else "unavailable_legacy_root"
            ),
            "compared_dates": compared_dates,
            "compared_rows": compared_rows,
            "excluded_partial_dates": excluded_partial_dates,
            "excluded_field_partial_dates": excluded_field_partial_dates,
            "excluded_selector_ambiguous_dates": excluded_selector_ambiguous_dates,
            "field_partial_evidence": field_partial_evidence,
            "maximum_absolute_difference": maxima,
            "absolute_tolerances": ABSOLUTE_TOLERANCES,
        },
        errors,
    )


def verify_hrrr_r5_rebuild(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    legacy_root: str | Path = LEGACY_DEFAULT,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run complete-range, provenance, pressure, and legacy-parity release gates."""

    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    errors: list[str] = []

    acquisition_manifest = load_manifest(weather.acquisition_manifest_path, verify_artifacts=True)
    weather_manifest = load_manifest(weather.manifest_path, verify_artifacts=True)
    if acquisition_manifest.get("product") != "meteorological.surface_weather.download":
        errors.append("Acquisition manifest product is incorrect.")
    if weather_manifest.get("product") != "meteorological.surface_weather":
        errors.append("Weather manifest product is incorrect.")
    acquisition_range = acquisition_manifest.get("temporal_coverage", {})
    weather_range = weather_manifest.get("temporal_coverage", {})
    if acquisition_range != weather_range:
        errors.append("Acquisition and weather manifests have different frozen ranges.")
    start = str(weather_range.get("start_date") or "")
    end = str(weather_range.get("end_date") or "")
    if start != weather.start_date or not end:
        errors.append("Weather manifest frozen range is invalid for the configured backfill.")
    dates = [value.strftime("%Y-%m-%d") for value in pd.date_range(start, end, freq="D")]
    expected_times = _expected_times(start, end, weather.timezone, weather.interval_hours)
    expected_time_keys = {value.tz_convert("UTC").isoformat() for value in expected_times}
    support = load_meteorological_support(weather.h3_resolution, config_path)
    support_cells = set(support["H3_INDEX"].astype(str))

    inventory_file = pq.ParquetFile(weather.inventory_path)
    if not inventory_file.schema_arrow.equals(INVENTORY_SCHEMA, check_metadata=False):
        errors.append("Canonical acquisition inventory schema is incorrect.")
    inventory = inventory_file.read().to_pandas()
    observed_time_keys = set(inventory["VALID_TIME_UTC"].astype(str))
    if len(inventory) != len(expected_times) or observed_time_keys != expected_time_keys:
        errors.append("Canonical acquisition inventory does not cover the frozen valid times.")
    if inventory["VALID_TIME_UTC"].duplicated().any():
        errors.append("Canonical acquisition inventory has duplicate valid times.")
    required_inventory_values = {
        "STATUS": "COMPLETE",
        "H3_RESOLUTION": weather.h3_resolution,
        "H3_CELL_COUNT": len(support_cells),
        "VARIABLE_COUNT": 9,
        "SOURCE_BACKEND": "grib",
        "SOURCE_FORMAT": "GRIB2",
        "PRECISION_QC_STATE": "NATIVE_GRIB_DECODING",
        "PRECIP_FORECAST_HOUR": weather.precipitation_forecast_hour,
    }
    for column, expected in required_inventory_values.items():
        if set(inventory[column]) != {expected}:
            errors.append(f"Canonical acquisition inventory has invalid {column} values.")
    if (
        inventory[
            [
                "RELATIVE_PATH",
                "CHECKSUM",
                "CROSSWALK_RELATIVE_PATH",
                "CROSSWALK_CHECKSUM",
                "SOURCE_GRID_HASH",
                "SOURCE_URI",
                "PRECIP_INIT_TIME_UTC",
                "PRECIP_SOURCE_URI",
            ]
        ]
        .isna()
        .any()
        .any()
    ):
        errors.append("Canonical acquisition inventory has incomplete per-row provenance.")
    invalid_source_uris = sum(
        str(row.SOURCE_URI) != hrrr_aws_archive_uri(pd.Timestamp(row.VALID_TIME_UTC))
        for row in inventory.itertuples(index=False)
    )
    if invalid_source_uris:
        errors.append(
            f"Canonical acquisition inventory has {invalid_source_uris} non-authoritative "
            "source URIs."
        )
    invalid_precip_source_uris = sum(
        str(row.PRECIP_SOURCE_URI)
        != hrrr_aws_archive_uri(
            pd.Timestamp(row.VALID_TIME_UTC), weather.precipitation_forecast_hour
        )
        for row in inventory.itertuples(index=False)
    )
    if invalid_precip_source_uris:
        errors.append(
            f"Canonical acquisition inventory has {invalid_precip_source_uris} "
            "non-authoritative precipitation source URIs."
        )

    sample_checksum_failures = 0
    crosswalk_checksum_failures = 0
    checked_crosswalks: set[tuple[str, str]] = set()
    for row in inventory.itertuples(index=False):
        sample = weather.raw_dir / str(row.RELATIVE_PATH)
        if not sample.exists() or sha256_file(sample) != str(row.CHECKSUM):
            sample_checksum_failures += 1
        crosswalk = weather.raw_dir / str(row.CROSSWALK_RELATIVE_PATH)
        key = (str(crosswalk), str(row.CROSSWALK_CHECKSUM))
        if key not in checked_crosswalks:
            checked_crosswalks.add(key)
            if not crosswalk.exists() or sha256_file(crosswalk) != str(row.CROSSWALK_CHECKSUM):
                crosswalk_checksum_failures += 1
    if sample_checksum_failures:
        errors.append(f"{sample_checksum_failures} timestamp sample checksums failed.")
    if crosswalk_checksum_failures:
        errors.append(f"{crosswalk_checksum_failures} source crosswalk checksums failed.")

    daily_files: dict[str, Path] = {}
    daily_rows = 0
    pressure_minimum = np.inf
    pressure_maximum = -np.inf
    precipitation_maximum = -np.inf
    precipitation_nonzero_rows = 0
    for path in sorted(weather.daily_output_dir.rglob("*.parquet")):
        date = _date_from_path(path)
        if date is None:
            errors.append(f"Daily partition has no date key: {path}")
            continue
        if date in daily_files:
            errors.append(f"Daily weather has duplicate partitions for {date}.")
            continue
        daily_files[date] = path
        parquet = pq.ParquetFile(path)
        if not parquet.schema_arrow.equals(DAILY_SCHEMA, check_metadata=False):
            errors.append(f"Daily weather schema is incorrect for {date}.")
            continue
        frame = parquet.read().to_pandas()
        daily_rows += len(frame)
        numeric = frame.select_dtypes(include=[np.number]).to_numpy(dtype=float)
        if (
            len(frame) != len(support_cells)
            or set(frame["H3_INDEX"].astype(str)) != support_cells
            or frame.duplicated(["H3_INDEX", "DATE"]).any()
        ):
            errors.append(f"Daily weather support or keys are invalid for {date}.")
        if frame.isna().any().any() or not np.isfinite(numeric).all():
            errors.append(f"Daily weather contains null or non-finite values for {date}.")
        if (
            set(frame["DATE"].astype(str)) != {date}
            or set(frame["SAMPLE_COUNT"].astype(int)) != {6}
            or set(frame["EXPECTED_SAMPLE_COUNT"].astype(int)) != {6}
            or set(frame["SAMPLE_COVERAGE_FRAC"].astype(float)) != {1.0}
            or set(frame["QC_STATE"].astype(str)) != {"COMPLETE"}
        ):
            errors.append(f"Daily weather strict QC contract failed for {date}.")
        pressure_min = frame["MEAN_SEA_LEVEL_PRESSURE_HPA_MIN"].to_numpy(dtype=float)
        pressure_mean = frame["MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN"].to_numpy(dtype=float)
        pressure_minimum = min(pressure_minimum, float(pressure_min.min()))
        pressure_maximum = max(pressure_maximum, float(pressure_mean.max()))
        if (
            (pressure_min > pressure_mean).any()
            or (pressure_min < 800.0).any()
            or (pressure_mean > 1100.0).any()
        ):
            errors.append(f"Daily pressure validation failed for {date}.")
        precipitation = frame["PRECIP_MM_DAY_ESTIMATE"].to_numpy(dtype=float)
        precipitation_maximum = max(precipitation_maximum, float(precipitation.max()))
        precipitation_nonzero_rows += int((precipitation > 0.0).sum())
    if set(daily_files) != set(dates):
        errors.append("Daily weather partitions do not cover the frozen date range exactly.")
    expected_daily_rows = len(dates) * len(support_cells)
    if daily_rows != expected_daily_rows:
        errors.append(f"Daily weather row count is {daily_rows}; expected {expected_daily_rows}.")
    if precipitation_nonzero_rows == 0 or not np.isfinite(precipitation_maximum):
        errors.append("Daily precipitation is degenerate or all zero across the frozen range.")

    sample_files_by_date: dict[str, list[Path]] = defaultdict(list)
    for row in inventory.itertuples(index=False):
        sample_files_by_date[str(row.LOCAL_DATE)].append(weather.raw_dir / str(row.RELATIVE_PATH))
    legacy_report, legacy_errors = _compare_legacy(
        legacy_root=Path(legacy_root),
        daily_files=daily_files,
        support_cells=support_cells,
        sample_files_by_date=dict(sample_files_by_date),
        interval_hours=weather.interval_hours,
    )
    errors.extend(legacy_errors)
    payload = {
        "schema_version": 1,
        "passed": not errors,
        "config_path": str(Path(config_path)),
        "frozen_range": {"start_date": start, "end_date": end},
        "expected_dates": len(dates),
        "expected_valid_times": len(expected_times),
        "support_cell_count": len(support_cells),
        "inventory_rows": len(inventory),
        "daily_rows": daily_rows,
        "sample_checksum_failures": sample_checksum_failures,
        "crosswalk_checksum_failures": crosswalk_checksum_failures,
        "pressure_validation": {
            "minimum_hpa": pressure_minimum,
            "maximum_hpa": pressure_maximum,
            "allowed_range_hpa": [800.0, 1100.0],
            "minimum_not_above_mean": True,
        },
        "precipitation_validation": {
            "source_forecast_hour": weather.precipitation_forecast_hour,
            "maximum_mm_day_estimate": precipitation_maximum,
            "nonzero_daily_rows": precipitation_nonzero_rows,
            "all_zero_rejected": True,
            "legacy_parity": "not_applicable_semantics_changed_from_f00_to_f01",
        },
        "legacy_comparison": legacy_report,
        "weather_manifest_path": str(weather.manifest_path),
        "weather_manifest_checksum": sha256_file(weather.manifest_path),
        "acquisition_manifest_path": str(weather.acquisition_manifest_path),
        "acquisition_manifest_checksum": sha256_file(weather.acquisition_manifest_path),
        "errors": errors,
    }
    destination = Path(output_path) if output_path else verification_report_path(config_path)
    atomic_write_json(destination, payload, overwrite=True)
    payload["report_path"] = str(destination)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--legacy-root", default=str(LEGACY_DEFAULT))
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = verify_hrrr_r5_rebuild(
        args.config, legacy_root=args.legacy_root, output_path=args.output
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
