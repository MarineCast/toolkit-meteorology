"""Offline, provenance-preserving comparisons to independent scientific references.

This software evaluates a supplied, prespecified protocol. It does not acquire reference
data, certify independence, or turn a synthetic fixture into an empirical validation.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import platform
import shutil
import tempfile
from typing import Any, Literal

import h3
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pyproj import Geod

from meteorology.artifacts import code_state, load_manifest, resolve_portable_path, sha256_file
from meteorology.astronomy import solar_altitude_deg
from meteorology._version import __version__
from meteorology.lunar.compute import moon_altitude_deg
from meteorology.validation import validate_product


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(Strict):
    name: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    retrieved_at_utc: datetime
    license: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    redistribution_restrictions: str = Field(min_length=1)
    may_share_assimilation_inputs: bool | None

    @model_validator(mode="after")
    def timestamp(self) -> Source:
        if self.retrieved_at_utc.tzinfo is None:
            raise ValueError("Reference retrieval timestamp needs a UTC offset")
        return self


class FileSpec(Strict):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Source | None = None


class Bundle(Strict):
    schema_version: Literal[1]
    evidence_class: Literal["synthetic_fixture", "independent_reference"]
    sites: FileSpec
    weather: FileSpec | None = None
    astronomy: FileSpec | None = None
    astronomy_conventions: dict[str, Any] | None = None

    @model_validator(mode="after")
    def sources_present(self) -> Bundle:
        if self.sites.source is not None:
            raise ValueError(
                "sites file has no separate source; source metadata belongs to each reference file"
            )
        if any(spec is not None and spec.source is None for spec in (self.weather, self.astronomy)):
            raise ValueError("Each reference file requires source metadata")
        return self


class Holdout(Strict):
    site_ids: list[str]
    start_utc: datetime
    end_utc: datetime
    astronomy_case_tags: list[str]

    @model_validator(mode="after")
    def interval(self) -> Holdout:
        if (
            self.start_utc.tzinfo is None
            or self.end_utc.tzinfo is None
            or self.start_utc >= self.end_utc
        ):
            raise ValueError("Holdout must have ordered timezone-aware endpoints")
        return self


class Pairing(Strict):
    max_time_delta_minutes: float = Field(ge=0)
    max_centroid_distance_m: float = Field(ge=0)
    max_source_distance_m: float = Field(ge=0)
    max_astronomy_coordinate_offset_m: float = Field(ge=0)
    height_tolerance_m: float = Field(ge=0)
    calm_speed_threshold_ms: float = Field(ge=0)
    wet_threshold_mm: float = Field(gt=0)
    precip_intensity_edges_mm: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def edges(self) -> Pairing:
        if any(
            x <= self.wet_threshold_mm or not math.isfinite(x)
            for x in self.precip_intensity_edges_mm
        ):
            raise ValueError("Precipitation intensity edges must exceed the wet threshold")
        if self.precip_intensity_edges_mm != sorted(set(self.precip_intensity_edges_mm)):
            raise ValueError("Precipitation intensity edges must be unique and increasing")
        return self


class Threshold(Strict):
    min_pairs: int = Field(gt=0)
    max_abs_bias: float = Field(ge=0)
    max_mae: float = Field(ge=0)
    max_rmse: float = Field(ge=0)
    rationale: str = Field(min_length=1)


class Protocol(Strict):
    schema_version: Literal[1]
    name: str = Field(min_length=1)
    locked_at_utc: datetime
    rationale: str = Field(min_length=1)
    holdout: Holdout
    pairing: Pairing
    accepted_quality_flags: list[str] = Field(min_length=1)
    thresholds: dict[str, Threshold]

    @model_validator(mode="after")
    def lock_and_thresholds(self) -> Protocol:
        if self.locked_at_utc.tzinfo is None:
            raise ValueError("Protocol lock time must include a UTC offset")
        if not self.thresholds:
            raise ValueError("At least one prespecified threshold is required")
        return self


WEATHER: dict[str, tuple[str, str, float | None, str]] = {
    "TEMPERATURE_2M_C": ("degC", "air_temperature_2m", 2, "TEMPERATURE_2M_C"),
    "RELATIVE_HUMIDITY_2M_PCT": ("percent", "relative_humidity_2m", 2, "RELATIVE_HUMIDITY_2M_PCT"),
    "U_WIND_10M_MS": ("m/s", "earth_relative_wind_10m", 10, "U_WIND_10M_MS"),
    "V_WIND_10M_MS": ("m/s", "earth_relative_wind_10m", 10, "V_WIND_10M_MS"),
    "MEAN_SEA_LEVEL_PRESSURE_HPA": (
        "hPa",
        "sea_level_reduced_pressure",
        None,
        "MEAN_SEA_LEVEL_PRESSURE_HPA",
    ),
    "VISIBILITY_KM": ("km", "horizontal_visibility_surface", None, "VISIBILITY_KM"),
    "TOTAL_CLOUD_COVER_PCT": ("percent", "total_column_cloud_cover", None, "TOTAL_CLOUD_COVER_PCT"),
    "PRECIP_4H_MM": ("mm", "gauge_4h_accumulation", None, "PRECIP_RATE_MM_HR"),
}
ASTRONOMY: dict[str, tuple[str, str, str]] = {
    "DAYLIGHT_HOURS": ("hour", "daylight", "DAYLIGHT_HOURS"),
    "SOLAR_ELEVATION_MAX_DEG": ("degree", "daylight", "SOLAR_ELEVATION_MAX_DEG"),
    "SOLAR_ALTITUDE_DEG_AT_UTC": ("degree", "daylight", "function"),
    "LUNAR_ILLUMINATION_FRACTION": ("fraction", "lunar", "LUNAR_ILLUMINATION_FRACTION"),
    "LUNAR_PHASE_ANGLE_DEG": ("degree", "lunar", "LUNAR_PHASE_ANGLE_DEG"),
    "MOON_ALTITUDE_DEG_AT_UTC": ("degree", "lunar", "function"),
    "MOON_VISIBLE_DARK_HOURS": ("hour", "lunar", "MOON_VISIBLE_DARK_HOURS"),
}
CASE_TAGS = ("equinox", "solstice", "leap_day", "polar", "spring_dst", "fall_dst")
GEOD = Geod(ellps="WGS84")
PAIRING_EXCLUSIONS = {
    "site_to_centroid_distance",
    "unmatched_time",
    "ambiguous_nearest_time",
    "unavailable_h3_cell",
    "source_grid_distance",
    "model_wind_basis",
    "incompatible_model_lead",
    "nonfinite_model_value",
    "coordinate_offset",
    "missing_or_duplicate_frozen_daily_row",
    "utc_instant_local_date_mismatch",
    "instant_for_daily_metric",
    "missing_vector_component",
    "vector_pair_mismatch",
    "calm_wind_direction",
}


def _read_json(path: Path, model: type[Strict]) -> Strict:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _checked_file(base: Path, spec: FileSpec) -> Path:
    candidate = (base / spec.path).resolve()
    if candidate == base or base not in candidate.parents or not candidate.is_file():
        raise ValueError(
            f"Reference path must be an existing file inside bundle directory: {spec.path}"
        )
    if sha256_file(candidate) != spec.sha256:
        raise ValueError(f"Reference SHA-256 mismatch: {spec.path}")
    return candidate


def _csv(path: Path, required: set[str]) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if missing := required - set(frame.columns):
        raise ValueError(f"{path.name} lacks columns: {sorted(missing)}")
    if not frame.empty and (frame[list(required)] == "").all(axis=1).any():
        raise ValueError(f"{path.name} contains a completely empty record")
    return frame


def _utc(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        raise ValueError("Timestamp needs a UTC offset")
    return ts.tz_convert("UTC")


def _number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Value must be finite")
    return number


def _reference_range(metric: str, value: float) -> bool:
    if metric in {"RELATIVE_HUMIDITY_2M_PCT", "TOTAL_CLOUD_COVER_PCT"}:
        return 0 <= value <= 100
    if metric in {"VISIBILITY_KM", "PRECIP_4H_MM"}:
        return value >= 0
    if metric == "MEAN_SEA_LEVEL_PRESSURE_HPA":
        return value > 0
    if metric == "LUNAR_ILLUMINATION_FRACTION":
        return 0 <= value <= 1
    if metric == "LUNAR_PHASE_ANGLE_DEG":
        return 0 <= value < 360
    if metric in {
        "SOLAR_ELEVATION_MAX_DEG",
        "SOLAR_ALTITUDE_DEG_AT_UTC",
        "MOON_ALTITUDE_DEG_AT_UTC",
    }:
        return -90 <= value <= 90
    if metric in {"DAYLIGHT_HOURS", "MOON_VISIBLE_DARK_HOURS"}:
        return 0 <= value <= 25
    return True


def _distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return float(GEOD.inv(lon1, lat1, lon2, lat2)[2])


def _installed_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _season(month: int) -> str:
    return ("DJF", "MAM", "JJA", "SON")[(month % 12) // 3]


def _exclusion(family: str, row: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "family": family,
        "record_id": row.get("record_id", ""),
        "site_id": row.get("site_id", ""),
        "h3_index": row.get("h3_index", ""),
        "metric": row.get("metric", ""),
        "valid_time_utc": row.get("valid_time_utc", ""),
        "date": row.get("date", ""),
        "reason": reason,
        "stage": "pairing" if reason in PAIRING_EXCLUSIONS else "eligibility",
    }


def _pair(
    family: str,
    row: dict[str, Any],
    *,
    metric: str,
    unit: str,
    reference: float,
    model: float,
    h3_index: str,
    valid_time: str,
    region: str,
    season: str,
    mode: str,
    centroid_m: float | None = None,
    source_m: float | None = None,
    case_tag: str = "",
    site_id: str = "",
    platform: str = "",
    elevation_m: float | None = None,
    site_source_uri: str = "",
    time_delta_minutes: float | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error = model - reference
    if metric in {"LUNAR_PHASE_ANGLE_DEG", "WIND_DIRECTION_10M_DEG"}:
        error = (error + 180) % 360 - 180
    return {
        "family": family,
        "record_id": row["record_id"],
        "site_id": site_id,
        "h3_index": h3_index,
        "date": row.get("date", ""),
        "valid_time_utc": valid_time,
        "reference_valid_time_utc": row.get("valid_time_utc", ""),
        "reference_interval_start_utc": row.get("interval_start_utc", ""),
        "reference_interval_end_utc": row.get("interval_end_utc", ""),
        "reference_sensor_height_m": row.get("height_m", ""),
        "time_delta_minutes": time_delta_minutes,
        "platform": platform,
        "site_elevation_m": elevation_m,
        "site_source_uri": site_source_uri,
        "metric": metric,
        "unit": unit,
        "reference_value": reference,
        "model_value": model,
        "signed_error": error,
        "region": region,
        "season": season,
        "source_mode": mode,
        "case_tag": case_tag,
        "centroid_distance_m": centroid_m,
        "source_grid_distance_m": source_m,
        **(provenance or {}),
    }


def _frozen(path: Path, product: str) -> dict[str, Any]:
    if path.name != "MANIFEST.json":
        raise ValueError(f"Expected frozen MANIFEST.json: {path}")
    payload = load_manifest(path)
    if payload["product"] != product:
        raise ValueError(f"Expected {product}, got {payload['product']}")
    if not (path.parent / "artifacts").is_dir() or not (path.parent / "inputs").is_dir():
        raise ValueError(f"Not a frozen release: {path}")
    if product == "meteorological.surface_weather" and not payload.get("archived_hrrr_samples"):
        raise ValueError("Frozen weather release lacks archived timestamp samples")
    for item in [
        *payload["artifacts"],
        *payload["inputs"],
        *(
            [payload["archived_hrrr_samples"]]
            if product == "meteorological.surface_weather"
            else []
        ),
    ]:
        relative = Path(item["path"])
        resolved = (path.parent / relative).resolve()
        if relative.is_absolute() or path.parent.resolve() not in resolved.parents:
            raise ValueError("Frozen release contains an external dependency")
    validate_product(path)
    return payload


def _weather_pairs(
    manifest_path: Path,
    manifest: dict[str, Any],
    sites: pd.DataFrame,
    references: pd.DataFrame,
    protocol: Protocol,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if sites["site_id"].duplicated().any():
        raise ValueError("Duplicate site_id in sites.csv")
    sites_by_id = {row["site_id"]: row for row in sites.to_dict("records")}
    inventory_spec = next(
        (
            item
            for item in manifest["inputs"]
            if str(item["path"]).endswith("SOURCE_INVENTORY.parquet")
        ),
        None,
    )
    if inventory_spec is None:
        raise ValueError("Frozen weather release lacks its source inventory")
    inventory_path = resolve_portable_path(inventory_spec["path"], base=manifest_path.parent)
    inventory = pq.read_table(inventory_path).to_pandas()
    if inventory["VALID_TIME_UTC"].duplicated().any() or set(inventory["STATUS"].astype(str)) != {
        "COMPLETE"
    }:
        raise ValueError("Frozen weather inventory needs unique complete valid times")
    by_time = {_utc(str(row.VALID_TIME_UTC)): row for row in inventory.itertuples(index=False)}
    times = sorted(by_time)
    if not times:
        raise ValueError("Frozen weather inventory is empty")
    cache: dict[str, pd.DataFrame] = {}
    pairs: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    vector_parts: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in references.to_dict("records"):
        reason = ""
        try:
            metric = record["metric"]
            if metric not in WEATHER:
                reason = "unsupported_metric"
            else:
                unit, definition, height, column = WEATHER[metric]
                site = sites_by_id.get(record["site_id"])
                if site is None:
                    reason = "unknown_site"
                elif record["quality_flag"] not in protocol.accepted_quality_flags:
                    reason = "quality_flag"
                elif record["unit"] != unit or record["definition"] != definition:
                    reason = "unit_or_definition_mismatch"
                elif record["site_id"] not in protocol.holdout.site_ids:
                    reason = "outside_holdout_site"
                else:
                    valid = _utc(record["valid_time_utc"])
                    record["date"] = (
                        valid.tz_convert(manifest["resolved_config"]["timezone"]).date().isoformat()
                    )
                    start = pd.Timestamp(protocol.holdout.start_utc).tz_convert("UTC")
                    end = pd.Timestamp(protocol.holdout.end_utc).tz_convert("UTC")
                    if not start <= valid <= end:
                        reason = "outside_holdout_time"
                    elif metric == "PRECIP_4H_MM" and (
                        not record["interval_start_utc"]
                        or not record["interval_end_utc"]
                        or _utc(record["interval_start_utc"]) != valid - pd.Timedelta(hours=4)
                        or _utc(record["interval_end_utc"]) != valid
                    ):
                        reason = "incompatible_precip_interval"
                    elif metric != "PRECIP_4H_MM" and (
                        record["interval_start_utc"] or record["interval_end_utc"]
                    ):
                        reason = "interval_reference_for_instantaneous_metric"
                    elif height is not None and (
                        not record["height_m"]
                        or abs(_number(record["height_m"]) - height)
                        > protocol.pairing.height_tolerance_m
                    ):
                        reason = "incompatible_sensor_height"
                    elif metric in {"U_WIND_10M_MS", "V_WIND_10M_MS"} and (
                        site["wind_basis"] != "earth_relative"
                        or not site["wind_height_m"]
                        or abs(_number(site["wind_height_m"]) - 10)
                        > protocol.pairing.height_tolerance_m
                    ):
                        reason = "incompatible_wind_basis_or_height"
                    else:
                        ref = _number(record["value"])
                        lat, lon = _number(site["latitude"]), _number(site["longitude"])
                        if not _reference_range(metric, ref):
                            reason = "invalid_reference_range"
                        elif not (-90 <= lat <= 90 and -180 <= lon <= 180):
                            raise ValueError("Invalid site coordinates")
                        elif (
                            not site["platform"]
                            or not site["region"]
                            or not site["source_site_uri"]
                            or not site["elevation_m"]
                        ):
                            reason = "invalid_site_metadata"
                        if not reason:
                            cell = str(h3.latlng_to_cell(lat, lon, 5))
                            centroid_lat, centroid_lon = h3.cell_to_latlng(cell)
                            centroid_m = _distance(lat, lon, centroid_lat, centroid_lon)
                            if centroid_m > protocol.pairing.max_centroid_distance_m:
                                reason = "site_to_centroid_distance"
                        if not reason:
                            position = bisect_left(times, valid)
                            neighbors = times[max(0, position - 1) : position + 1]
                            nearest = min(neighbors, key=lambda x: abs(x - valid))
                            delta = abs((nearest - valid).total_seconds()) / 60
                            if delta > protocol.pairing.max_time_delta_minutes:
                                reason = "unmatched_time"
                            elif sum(abs(t - valid) == abs(nearest - valid) for t in neighbors) > 1:
                                reason = "ambiguous_nearest_time"
                            else:
                                inventory_row = by_time[nearest]
                                sample_path = (
                                    inventory_path.parent / str(inventory_row.RELATIVE_PATH)
                                ).resolve()
                                if inventory_path.parent.resolve() not in sample_path.parents:
                                    raise ValueError("Inventory sample escapes frozen release")
                                if str(nearest) not in cache:
                                    if len(cache) >= 8:
                                        cache.pop(next(iter(cache)))
                                    cache[str(nearest)] = (
                                        pq.read_table(sample_path).to_pandas().set_index("H3_INDEX")
                                    )
                                sample = cache[str(nearest)]
                                if cell not in sample.index:
                                    reason = "unavailable_h3_cell"
                                else:
                                    model_row = sample.loc[cell]
                                    source_m = float(model_row["SOURCE_GRID_DISTANCE_M"])
                                    if source_m > protocol.pairing.max_source_distance_m:
                                        reason = "source_grid_distance"
                                    elif (
                                        metric in {"U_WIND_10M_MS", "V_WIND_10M_MS"}
                                        and model_row["WIND_VECTOR_BASIS"] != "earth_relative"
                                    ):
                                        reason = "model_wind_basis"
                                    elif (
                                        model_row["SOURCE_PRODUCT"] != "sfc"
                                        or int(model_row["FORECAST_HOUR"]) != 0
                                        or (
                                            metric == "PRECIP_4H_MM"
                                            and int(model_row["PRECIP_FORECAST_HOUR"]) != 1
                                        )
                                    ):
                                        reason = "incompatible_model_lead"
                                    else:
                                        model = float(model_row[column]) * (
                                            4 if metric == "PRECIP_4H_MM" else 1
                                        )
                                        if not math.isfinite(model):
                                            reason = "nonfinite_model_value"
                                        else:
                                            paired = _pair(
                                                "weather",
                                                record,
                                                metric=metric,
                                                unit=unit,
                                                reference=ref,
                                                model=model,
                                                h3_index=cell,
                                                valid_time=nearest.isoformat(),
                                                region=site["region"],
                                                season=_season(pd.Timestamp(record["date"]).month),
                                                mode="sampling_sensitivity"
                                                if metric == "PRECIP_4H_MM"
                                                else "frozen_timestamp_sample",
                                                centroid_m=centroid_m,
                                                source_m=source_m,
                                                site_id=record["site_id"],
                                                platform=site["platform"],
                                                elevation_m=_number(site["elevation_m"]),
                                                site_source_uri=site["source_site_uri"],
                                                time_delta_minutes=delta,
                                                provenance={
                                                    "source_model": str(model_row["SOURCE_MODEL"]),
                                                    "source_product": str(
                                                        model_row["SOURCE_PRODUCT"]
                                                    ),
                                                    "forecast_hour": int(
                                                        model_row["FORECAST_HOUR"]
                                                    ),
                                                    "precip_forecast_hour": int(
                                                        model_row["PRECIP_FORECAST_HOUR"]
                                                    ),
                                                    "source_grid_hash": str(
                                                        model_row["SOURCE_GRID_HASH"]
                                                    ),
                                                    "sample_checksum": str(inventory_row.CHECKSUM),
                                                    "crosswalk_checksum": str(
                                                        inventory_row.CROSSWALK_CHECKSUM
                                                    ),
                                                    "source_uri": str(inventory_row.SOURCE_URI),
                                                    "source_object_uri": str(
                                                        inventory_row.SOURCE_OBJECT_URI or ""
                                                    ),
                                                    "source_retrieved_at_utc": str(
                                                        inventory_row.SOURCE_RETRIEVED_AT_UTC or ""
                                                    ),
                                                    "precip_source_uri": str(
                                                        inventory_row.PRECIP_SOURCE_URI
                                                    ),
                                                    "precip_object_uri": str(
                                                        inventory_row.PRECIP_OBJECT_URI or ""
                                                    ),
                                                    "precip_retrieved_at_utc": str(
                                                        inventory_row.PRECIP_RETRIEVED_AT_UTC or ""
                                                    ),
                                                },
                                            )
                                            pairs.append(paired)
                                            if metric in {"U_WIND_10M_MS", "V_WIND_10M_MS"}:
                                                vector_parts[
                                                    (record["site_id"], valid.isoformat())
                                                ][metric] = paired
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reason = f"invalid_reference:{type(exc).__name__}"
        if reason:
            excluded.append(_exclusion("weather", record, reason))
    for (site_id, valid), parts in vector_parts.items():
        if not all(k in parts for k in ("U_WIND_10M_MS", "V_WIND_10M_MS")):
            excluded.append(
                {
                    "family": "weather",
                    "record_id": f"derived:{site_id}:{valid}",
                    "site_id": site_id,
                    "h3_index": "",
                    "metric": "WIND_VECTOR_10M",
                    "valid_time_utc": valid,
                    "date": "",
                    "reason": "missing_vector_component",
                    "stage": "pairing",
                }
            )
            continue
        u, v = parts["U_WIND_10M_MS"], parts["V_WIND_10M_MS"]
        if u["h3_index"] != v["h3_index"] or u["valid_time_utc"] != v["valid_time_utc"]:
            excluded.append(
                _exclusion(
                    "weather",
                    {
                        "record_id": f"derived:{site_id}:{valid}",
                        "site_id": site_id,
                        "valid_time_utc": valid,
                        "metric": "WIND_VECTOR_10M",
                    },
                    "vector_pair_mismatch",
                )
            )
            continue
        synthetic = {
            "record_id": f"derived:{u['record_id']}+{v['record_id']}",
            "date": u["date"],
            "site_id": site_id,
            "valid_time_utc": valid,
            "height_m": "10",
        }
        common = {
            "h3_index": u["h3_index"],
            "valid_time": u["valid_time_utc"],
            "region": u["region"],
            "season": u["season"],
            "mode": "derived_from_paired_uv",
            "centroid_m": u["centroid_distance_m"],
            "source_m": u["source_grid_distance_m"],
            "site_id": site_id,
            "platform": u["platform"],
            "elevation_m": u["site_elevation_m"],
            "site_source_uri": u["site_source_uri"],
            "time_delta_minutes": u["time_delta_minutes"],
            "provenance": {
                key: u.get(key)
                for key in (
                    "source_model",
                    "source_product",
                    "forecast_hour",
                    "precip_forecast_hour",
                    "source_grid_hash",
                    "sample_checksum",
                    "crosswalk_checksum",
                    "source_uri",
                    "source_object_uri",
                    "source_retrieved_at_utc",
                    "precip_source_uri",
                    "precip_object_uri",
                    "precip_retrieved_at_utc",
                )
            },
        }
        reference_speed = math.hypot(u["reference_value"], v["reference_value"])
        model_speed = math.hypot(u["model_value"], v["model_value"])
        pairs.append(
            _pair(
                "weather",
                synthetic,
                metric="WIND_SPEED_10M_MS",
                unit="m/s",
                reference=reference_speed,
                model=model_speed,
                **common,
            )
        )
        if min(reference_speed, model_speed) < protocol.pairing.calm_speed_threshold_ms:
            excluded.append(
                _exclusion(
                    "weather",
                    {"record_id": synthetic["record_id"], "metric": "WIND_DIRECTION_10M_DEG"},
                    "calm_wind_direction",
                )
            )
        else:
            ref_dir = math.degrees(math.atan2(-u["reference_value"], -v["reference_value"])) % 360
            model_dir = math.degrees(math.atan2(-u["model_value"], -v["model_value"])) % 360
            pairs.append(
                _pair(
                    "weather",
                    synthetic,
                    metric="WIND_DIRECTION_10M_DEG",
                    unit="degree",
                    reference=ref_dir,
                    model=model_dir,
                    **common,
                )
            )
    return pairs, excluded


def _astronomy_pairs(
    paths: dict[str, Path],
    manifests: dict[str, dict[str, Any]],
    bundle: Bundle,
    references: pd.DataFrame,
    protocol: Protocol,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conventions = bundle.astronomy_conventions or {}
    family_keys = {
        "daylight": ("timezone", "timestep_minutes", "low_sun_max_degrees", "leap_day_policy"),
        "lunar": (
            "timezone",
            "timestep_minutes",
            "sample_hour_utc",
            "dark_sun_altitude_deg",
            "moon_altitude_min_deg",
        ),
    }
    horizon_matches = (
        conventions.get("horizon") == "geometric" and conventions.get("refraction") == "none"
    )
    comparable = {
        family: horizon_matches
        and conventions.get(family)
        == {key: manifest["resolved_config"][key] for key in family_keys[family]}
        for family, manifest in manifests.items()
    }
    datasets = {
        family: ds.dataset(
            resolve_portable_path(manifest["artifacts"][0]["path"], base=paths[family].parent),
            format="parquet",
            partitioning="hive",
        )
        for family, manifest in manifests.items()
    }
    cache: dict[tuple[str, str, str], pd.DataFrame] = {}
    pairs: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for record in references.to_dict("records"):
        reason = ""
        try:
            metric = record["metric"]
            if metric not in ASTRONOMY:
                reason = "unsupported_metric"
            else:
                unit, family, column = ASTRONOMY[metric]
                if family not in manifests:
                    reason = "release_not_selected"
                elif record["unit"] != unit:
                    reason = "unit_mismatch"
                elif record["quality_flag"] not in protocol.accepted_quality_flags:
                    reason = "quality_flag"
                elif not comparable[family]:
                    reason = "convention_mismatch"
                elif record["case_tag"] not in protocol.holdout.astronomy_case_tags:
                    reason = "outside_holdout_case"
                else:
                    date = pd.Timestamp(record["date"])
                    if date.tzinfo is not None or date.strftime("%Y-%m-%d") != record["date"]:
                        reason = "invalid_local_date"
                    else:
                        zone = manifests[family]["resolved_config"]["timezone"]
                        day_start = date.tz_localize(zone).tz_convert("UTC")
                        day_end = (date + pd.Timedelta(days=1)).tz_localize(zone).tz_convert("UTC")
                        holdout_start = pd.Timestamp(protocol.holdout.start_utc).tz_convert("UTC")
                        holdout_end = pd.Timestamp(protocol.holdout.end_utc).tz_convert("UTC")
                        if not (holdout_start <= day_start and day_end <= holdout_end):
                            reason = "outside_holdout_time"
                    if not reason:
                        cell = str(record["h3_index"])
                        if (
                            not h3.is_valid_cell(cell)
                            or h3.get_resolution(cell) != manifests[family]["h3_resolution"]
                        ):
                            reason = "invalid_h3_cell"
                        else:
                            lat, lon = _number(record["latitude"]), _number(record["longitude"])
                            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                                raise ValueError("Invalid astronomy coordinates")
                            centroid_lat, centroid_lon = h3.cell_to_latlng(cell)
                            offset = _distance(lat, lon, centroid_lat, centroid_lon)
                            if offset > protocol.pairing.max_astronomy_coordinate_offset_m:
                                reason = "coordinate_offset"
                            else:
                                reference = _number(record["value"])
                                if not _reference_range(metric, reference):
                                    reason = "invalid_reference_range"
                                valid_string = ""
                                if not reason and column == "function":
                                    valid = _utc(record["valid_time_utc"])
                                    zone = manifests[family]["resolved_config"]["timezone"]
                                    if valid.tz_convert(zone).date() != date.date():
                                        reason = "utc_instant_local_date_mismatch"
                                    elif not (
                                        pd.Timestamp(protocol.holdout.start_utc).tz_convert("UTC")
                                        <= valid
                                        <= pd.Timestamp(protocol.holdout.end_utc).tz_convert("UTC")
                                    ):
                                        reason = "outside_holdout_time"
                                    else:
                                        fn = (
                                            solar_altitude_deg
                                            if family == "daylight"
                                            else moon_altitude_deg
                                        )
                                        model = float(
                                            fn(valid, np.array([lat]), np.array([lon]))[0]
                                        )
                                        valid_string = valid.isoformat()
                                        mode = "production_function"
                                elif not reason and record["valid_time_utc"]:
                                    reason = "instant_for_daily_metric"
                                elif not reason:
                                    key = (family, cell, record["date"])
                                    if key not in cache:
                                        dataset = datasets[family]
                                        cache[key] = dataset.to_table(
                                            filter=(ds.field("H3_INDEX") == cell)
                                            & (ds.field("DATE") == record["date"])
                                        ).to_pandas()
                                    rows = cache[key]
                                    if len(rows) != 1:
                                        reason = "missing_or_duplicate_frozen_daily_row"
                                    else:
                                        model = float(rows.iloc[0][column])
                                        mode = "frozen_daily_release"
                                if not reason:
                                    if not math.isfinite(model):
                                        reason = "nonfinite_model_value"
                                    else:
                                        pairs.append(
                                            _pair(
                                                "astronomy",
                                                record,
                                                metric=metric,
                                                unit=unit,
                                                reference=reference,
                                                model=model,
                                                h3_index=cell,
                                                valid_time=valid_string,
                                                region="",
                                                season=_season(date.month),
                                                mode=mode,
                                                centroid_m=offset,
                                                case_tag=record["case_tag"],
                                            )
                                        )
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reason = f"invalid_reference:{type(exc).__name__}"
        if reason:
            excluded.append(_exclusion("astronomy", record, reason))
    return pairs, excluded


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"status": "NOT_EVALUATED", "pair_count": 0, "bias": None, "mae": None, "rmse": None}
    errors = np.array([row["signed_error"] for row in rows], dtype=float)
    return {
        "status": "EVALUATED",
        "pair_count": len(rows),
        "bias": float(errors.mean()),
        "mae": float(np.abs(errors).mean()),
        "rmse": float(np.sqrt(np.square(errors).mean())),
    }


def _precip_diagnostics(rows: list[dict[str, Any]], protocol: Protocol) -> dict[str, Any]:
    if not rows:
        return {"status": "NOT_EVALUATED", "pair_count": 0}
    wet = protocol.pairing.wet_threshold_mm
    counts = Counter((r["reference_value"] >= wet, r["model_value"] >= wet) for r in rows)
    edges = [wet, *protocol.pairing.precip_intensity_edges_mm, float("inf")]
    bins = []
    for lower, upper in zip(edges, edges[1:]):
        selected = [r for r in rows if lower <= r["reference_value"] < upper]
        bins.append(
            {
                "reference_min_mm": lower,
                "reference_max_mm": None if math.isinf(upper) else upper,
                "metrics": _metrics(selected),
            }
        )
    return {
        "status": "SAMPLING_SENSITIVITY",
        "pair_count": len(rows),
        "wet_threshold_mm": wet,
        "occurrence": {
            "true_positive": counts[(True, True)],
            "false_positive": counts[(False, True)],
            "true_negative": counts[(False, False)],
            "false_negative": counts[(True, False)],
        },
        "reference_intensity_bins": bins,
        "meaning": "f01 instantaneous PRATE multiplied by four hours versus measured four-hour accumulation",
    }


def _summarize(
    pairs: list[dict[str, Any]], excluded: list[dict[str, Any]], protocol: Protocol, bundle: Bundle
) -> dict[str, Any]:
    by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pairs:
        by_metric[row["metric"]].append(row)
    present = set(by_metric) | {
        row["metric"] for row in excluded if row["metric"] in WEATHER | ASTRONOMY
    }
    if missing := present - set(protocol.thresholds):
        raise ValueError(
            f"Protocol lacks prespecified thresholds for reference metrics: {sorted(missing)}"
        )
    summary: dict[str, Any] = {}
    for metric in sorted(set(protocol.thresholds) | present):
        rows = by_metric.get(metric, [])
        calculated = _metrics(rows)
        threshold = protocol.thresholds[metric]
        if metric == "PRECIP_4H_MM":
            decision = "SAMPLING_SENSITIVITY"
        elif len(rows) < threshold.min_pairs:
            decision = "NOT_EVALUATED"
        else:
            decision = (
                "PASS"
                if (
                    abs(calculated["bias"]) <= threshold.max_abs_bias
                    and calculated["mae"] <= threshold.max_mae
                    and calculated["rmse"] <= threshold.max_rmse
                )
                else "FAIL"
            )
        strata: dict[str, dict[str, Any]] = {}
        for dimension in ("region", "season"):
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                groups[row[dimension] or "UNSPECIFIED"].append(row)
            strata[dimension] = {key: _metrics(value) for key, value in sorted(groups.items())}
        summary[metric] = {
            **calculated,
            "candidate_count": len(rows) + sum(row["metric"] == metric for row in excluded),
            "eligible_count": len(rows)
            + sum(row["metric"] == metric and row["stage"] == "pairing" for row in excluded),
            "excluded_count": sum(row["metric"] == metric for row in excluded),
            "threshold": threshold.model_dump(),
            "decision": decision,
            "strata": strata,
        }
    tags = {row["case_tag"] for row in pairs if row["family"] == "astronomy" and row["case_tag"]}
    decisions = [item["decision"] for key, item in summary.items() if key != "PRECIP_4H_MM"]
    if bundle.evidence_class == "synthetic_fixture":
        gate = "SOFTWARE_ONLY"
    else:
        gate = "REFERENCE_COMPARISON_UNREVIEWED"
    sharing = [
        spec.source.may_share_assimilation_inputs
        for spec in (bundle.weather, bundle.astronomy)
        if spec is not None and spec.source is not None
    ]
    independence = (
        "SHARED_INPUTS_DECLARED"
        if True in sharing
        else "UNKNOWN"
        if None in sharing
        else "NO_SHARED_INPUTS_DECLARED"
    )
    return {
        "evidence_gate": gate,
        "reference_independence_state": independence,
        "threshold_gate": "FAIL"
        if "FAIL" in decisions
        else ("NOT_EVALUATED" if not decisions or "NOT_EVALUATED" in decisions else "PASS"),
        "pair_count": len(pairs),
        "excluded_count": len(excluded),
        "exclusion_reasons": dict(sorted(Counter(row["reason"] for row in excluded).items())),
        "metrics": summary,
        "precipitation": _precip_diagnostics(by_metric.get("PRECIP_4H_MM", []), protocol),
        "astronomy_case_coverage": {
            "represented": sorted(tags),
            "missing": sorted(set(CASE_TAGS) - tags),
            "required": list(CASE_TAGS),
        },
    }


def _parquet(path: Path, records: list[dict[str, Any]], columns: list[str]) -> None:
    frame = pd.DataFrame.from_records(records, columns=columns)
    # Explicit empty string columns keep a stable inspectable Parquet schema on zero-row runs.
    if frame.empty:
        frame = frame.astype({name: "string" for name in columns})
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path)


def _report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# Scientific comparison report",
        "",
        f"Evidence gate: **{summary['evidence_gate']}**; threshold gate: **{summary['threshold_gate']}**.",
        "",
        f"Paired rows: {summary['pair_count']}; excluded rows: {summary['excluded_count']}.",
        "",
        "| Metric | Pairs | Excluded | Bias | MAE | RMSE | Decision |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]

    def display(value: float | None) -> str:
        return "—" if value is None else f"{value:.6g}"

    for metric, item in summary["metrics"].items():
        lines.append(
            f"| `{metric}` | {item['pair_count']} | {item['excluded_count']} | "
            f"{display(item['bias'])} | {display(item['mae'])} | {display(item['rmse'])} | {item['decision']} |"
        )
    lines += [
        "",
        "## Provenance and scope",
        "",
        f"Protocol: `{result['protocol']['name']}` (SHA-256 `{result['protocol']['sha256']}`).",
        f"Reference bundle SHA-256: `{result['reference_bundle']['sha256']}`; declared class: `{result['reference_bundle']['evidence_class']}`.",
        f"Assimilation overlap: `{summary['reference_independence_state']}`.",
        "",
        "### Producer releases",
        "",
    ]
    for family, release in result["releases"].items():
        lines.append(
            f"- {family}: release `{release['release_id']}`, method `{release['method_version']}`, "
            f"producer revision `{release['code_revision']}`, manifest SHA-256 `{release['manifest_sha256']}`."
        )
    lines += ["", "### Reference inputs", ""]
    for family in ("weather", "astronomy"):
        spec = result["reference_bundle"].get(family)
        if spec is not None:
            source = spec["source"]
            lines.append(
                f"- {family}: {source['name']} ({source['uri']}); retrieved {source['retrieved_at_utc']}; "
                f"SHA-256 `{spec['sha256']}`; license {source['license']}; "
                f"restrictions {source['redistribution_restrictions']}; "
                f"may share assimilation inputs: {source['may_share_assimilation_inputs']}."
            )
    lines += [
        "",
        "Exact producer artifact and input checksums, source attribution and holdout are in `results.json`.",
        "Region and season strata, occurrence and intensity diagnostics, thresholds, and exclusions are in `results.json` and the Parquet tables.",
        "A synthetic comparison verifies software behavior only. Real-reference independence, assimilation overlap, station representativeness and rights require separate review.",
        "Four-hour precipitation is a sampling-sensitivity diagnostic, not an accumulation accuracy gate.",
        f"Astronomy cases absent from paired records: {', '.join(summary['astronomy_case_coverage']['missing']) or 'none'}.",
        "No network acquisition was performed by this command.",
        "",
    ]
    return "\n".join(lines)


def run(
    *,
    weather_manifest: Path | None,
    daylight_manifest: Path | None,
    lunar_manifest: Path | None,
    reference_bundle: Path,
    protocol_path: Path,
    output_dir: Path,
) -> Path:
    """Validate all inputs, compute auditable pairs, and publish a new result atomically."""
    if not any((weather_manifest, daylight_manifest, lunar_manifest)):
        raise ValueError("Select at least one frozen producer manifest")
    if output_dir.exists():
        raise FileExistsError(f"Validation output already exists: {output_dir}")
    bundle = _read_json(reference_bundle, Bundle)
    protocol = _read_json(protocol_path, Protocol)
    base = reference_bundle.resolve().parent
    sites_path = _checked_file(base, bundle.sites)
    weather_path = _checked_file(base, bundle.weather) if bundle.weather else None
    astronomy_path = _checked_file(base, bundle.astronomy) if bundle.astronomy else None
    if bool(weather_manifest) != bool(weather_path):
        raise ValueError("Weather manifest and reference file must be supplied together")
    if bool(daylight_manifest or lunar_manifest) != bool(astronomy_path):
        raise ValueError("Astronomy manifest and reference file must be supplied together")
    manifests: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for family, path, product in (
        ("weather", weather_manifest, "meteorological.surface_weather"),
        ("daylight", daylight_manifest, "meteorological.daylight"),
        ("lunar", lunar_manifest, "meteorological.lunar"),
    ):
        if path is not None:
            paths[family] = path.resolve()
            manifests[family] = _frozen(path.resolve(), product)
    sites = _csv(
        sites_path,
        {
            "site_id",
            "latitude",
            "longitude",
            "elevation_m",
            "region",
            "platform",
            "wind_height_m",
            "wind_basis",
            "source_site_uri",
        },
    )
    pairs: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    input_counts: dict[str, int] = {}
    if weather_path:
        refs = _csv(
            weather_path,
            {
                "record_id",
                "site_id",
                "valid_time_utc",
                "metric",
                "value",
                "unit",
                "definition",
                "quality_flag",
                "height_m",
                "interval_start_utc",
                "interval_end_utc",
            },
        )
        if refs["record_id"].duplicated().any() or (refs["record_id"] == "").any():
            raise ValueError("Weather reference record_id must be unique and nonempty")
        if refs.duplicated(["site_id", "valid_time_utc", "metric"]).any():
            raise ValueError("Weather reference has duplicate site/time/metric grain")
        input_counts["weather"] = len(refs)
        a, b = _weather_pairs(paths["weather"], manifests["weather"], sites, refs, protocol)
        pairs.extend(a)
        excluded.extend(b)
    if astronomy_path:
        refs = _csv(
            astronomy_path,
            {
                "record_id",
                "h3_index",
                "date",
                "valid_time_utc",
                "metric",
                "value",
                "unit",
                "latitude",
                "longitude",
                "quality_flag",
                "case_tag",
            },
        )
        if refs["record_id"].duplicated().any() or (refs["record_id"] == "").any():
            raise ValueError("Astronomy reference record_id must be unique and nonempty")
        input_counts["astronomy"] = len(refs)
        a, b = _astronomy_pairs(
            {k: v for k, v in paths.items() if k != "weather"},
            {k: v for k, v in manifests.items() if k != "weather"},
            bundle,
            refs,
            protocol,
        )
        pairs.extend(a)
        excluded.extend(b)
    summary = _summarize(pairs, excluded, protocol, bundle)
    result = {
        "schema_version": 1,
        "workflow": "meteorology.scientific_validation.v1",
        "protocol": {
            **protocol.model_dump(mode="json"),
            "path": str(protocol_path.resolve()),
            "sha256": sha256_file(protocol_path),
        },
        "reference_bundle": {
            **bundle.model_dump(mode="json"),
            "sha256": sha256_file(reference_bundle),
            "bundle_path": str(reference_bundle.resolve()),
            "verified_files": {
                key: spec.sha256
                for key, spec in (
                    ("sites", bundle.sites),
                    ("weather", bundle.weather),
                    ("astronomy", bundle.astronomy),
                )
                if spec is not None
            },
        },
        "releases": {
            family: {
                "manifest_path": str(paths[family]),
                "manifest_sha256": sha256_file(paths[family]),
                "release_id": manifest["release_id"],
                "method_version": manifest["method_version"],
                "code_revision": manifest["code_revision"],
                "code_source_hash": manifest["code_source_hash"],
                "software_version": manifest["software_version"],
                "artifact_checksums": [item["checksum"] for item in manifest["artifacts"]],
                "input_checksums": [item["checksum"] for item in manifest["inputs"]],
                "archived_hrrr_samples_checksum": (manifest.get("archived_hrrr_samples") or {}).get(
                    "checksum"
                ),
                "temporal_coverage": manifest["temporal_coverage"],
                "spatial_bounds_wgs84": manifest["spatial_bounds_wgs84"],
            }
            for family, manifest in manifests.items()
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "meteorology": __version__,
            "installed_distribution": _installed_version("toolkit-meteorology"),
            "numpy": version("numpy"),
            "pandas": version("pandas"),
            "pyarrow": version("pyarrow"),
            "h3": version("h3"),
            "pyproj": version("pyproj"),
            "pydantic": version("pydantic"),
            "validator_code": code_state(),
        },
        "input_counts": input_counts,
        "summary": summary,
        "limitations": [
            "No reference provider acquisition occurred in this run.",
            "Declared independence, rights, and assimilation overlap are not independently verified by software.",
            "Station-to-grid comparison retains point-versus-area representativeness error.",
            "The retrospective f00 weather sample is not an as-issued forecast skill evaluation.",
            "Frozen sample hashes do not certify original GRIB object or cropped-message bytes.",
            "Instantaneous astronomy function comparisons are not frozen daily artifact comparisons.",
        ],
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-staging-", dir=output_dir.parent))
    try:
        pair_columns = [
            "family",
            "record_id",
            "site_id",
            "h3_index",
            "date",
            "valid_time_utc",
            "metric",
            "unit",
            "reference_value",
            "model_value",
            "signed_error",
            "region",
            "season",
            "source_mode",
            "case_tag",
            "centroid_distance_m",
            "source_grid_distance_m",
            "reference_valid_time_utc",
            "reference_interval_start_utc",
            "reference_interval_end_utc",
            "reference_sensor_height_m",
            "time_delta_minutes",
            "platform",
            "site_elevation_m",
            "site_source_uri",
            "source_model",
            "source_product",
            "forecast_hour",
            "precip_forecast_hour",
            "source_grid_hash",
            "sample_checksum",
            "crosswalk_checksum",
            "source_uri",
            "source_object_uri",
            "source_retrieved_at_utc",
            "precip_source_uri",
            "precip_object_uri",
            "precip_retrieved_at_utc",
        ]
        exclusion_columns = [
            "family",
            "record_id",
            "site_id",
            "h3_index",
            "metric",
            "valid_time_utc",
            "date",
            "reason",
            "stage",
        ]
        _parquet(staging / "paired.parquet", pairs, pair_columns)
        _parquet(staging / "excluded.parquet", excluded, exclusion_columns)
        (staging / "results.json").write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
        (staging / "REPORT.md").write_text(_report(result), encoding="utf-8")
        if output_dir.exists():
            raise FileExistsError(f"Validation output already exists: {output_dir}")
        staging.rename(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weather-manifest", type=Path)
    parser.add_argument("--daylight-manifest", type=Path)
    parser.add_argument("--lunar-manifest", type=Path)
    parser.add_argument("--reference-bundle", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        run(
            weather_manifest=args.weather_manifest,
            daylight_manifest=args.daylight_manifest,
            lunar_manifest=args.lunar_manifest,
            reference_bundle=args.reference_bundle,
            protocol_path=args.protocol,
            output_dir=args.output_dir,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
