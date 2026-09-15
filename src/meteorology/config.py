"""Validated configuration shared by meteorological products."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from meteorology.core.config.common_areas import bbox_from_config
from meteorology.core.config.data import load_data_config
from meteorology.core.config.paths import resolve_config_path, resolve_project_path

DEFAULT_CONFIG_PATH = "config/data/environment_meteorological.yaml"


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping.")
    return dict(value)


def _keys(section: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(section).difference(allowed))
    if unknown:
        raise ValueError(f"Unknown keys in {label}: {unknown}")


def _required(section: Mapping[str, Any], key: str, label: str) -> Any:
    value = section.get(key)
    if value in (None, ""):
        raise ValueError(f"Missing required setting: {label}.{key}")
    return value


def _path(value: Any) -> Path:
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else resolve_project_path(path)


def _date(value: Any, label: str) -> str:
    try:
        parsed = pd.Timestamp(str(value)).normalize()
    except Exception as exc:
        raise ValueError(f"{label} must be a valid ISO date.") from exc
    if pd.isna(parsed):
        raise ValueError(f"{label} must be a valid ISO date.")
    return parsed.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class SurfaceWeatherConfig:
    h3_resolution: int
    interval_hours: int
    precipitation_forecast_hour: int
    start_date: str
    end_date: str
    timezone: str
    availability_lag_hours: int
    raw_dir: Path
    working_inventory_path: Path
    inventory_path: Path
    acquisition_manifest_path: Path
    daily_output_dir: Path
    manifest_path: Path
    bbox_padding_degrees: float

    @property
    def expected_samples_per_standard_day(self) -> int:
        return 24 // self.interval_hours

    def resolved_end_date(self, now: datetime | pd.Timestamp | None = None) -> str:
        if self.end_date != "latest_complete":
            return self.end_date
        current = pd.Timestamp(now or datetime.now(tz=ZoneInfo(self.timezone)))
        if current.tzinfo is None:
            current = current.tz_localize(self.timezone)
        else:
            current = current.tz_convert(self.timezone)
        cutoff = current - pd.Timedelta(hours=self.availability_lag_hours)
        last_sample_hour = 24 - self.interval_hours
        complete = cutoff.normalize()
        if cutoff.hour < last_sample_hour:
            complete -= pd.Timedelta(days=1)
        return complete.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class DaylightConfig:
    h3_resolution: int
    start_date: str
    end_date: str
    timezone: str
    timestep_minutes: int
    low_sun_max_degrees: float
    default_weight: str
    daily_output_dir: Path
    day_of_year_output_path: Path
    manifest_path: Path


@dataclass(frozen=True)
class LunarConfig:
    h3_resolution: int
    start_date: str
    end_date: str
    timezone: str
    timestep_minutes: int
    sample_hour_utc: int
    dark_sun_altitude_deg: float
    moon_altitude_min_deg: float
    daily_output_dir: Path
    manifest_path: Path


@dataclass(frozen=True)
class MeteorologicalConfig:
    path: Path
    raw: dict[str, Any]
    schema_version: int
    bbox: dict[str, float]
    support_resolutions: tuple[int, ...]
    support_output_dir: Path
    support_manifest_path: Path
    surface_weather: SurfaceWeatherConfig
    daylight: DaylightConfig
    lunar: LunarConfig

    def support_path(self, resolution: int) -> Path:
        resolution = int(resolution)
        if resolution not in self.support_resolutions:
            raise ValueError(
                f"H3 resolution {resolution} is not configured; expected {self.support_resolutions}."
            )
        return self.support_output_dir / f"H3_MODEL_AREA_SUPPORT_RES_{resolution}.parquet"


def _validate_resolution(value: Any, label: str) -> int:
    resolution = int(value)
    if not 0 <= resolution <= 15:
        raise ValueError(f"{label} must be in [0, 15].")
    return resolution


def _load_surface(section: Mapping[str, Any]) -> SurfaceWeatherConfig:
    allowed = {
        "source",
        "sampling",
        "time",
        "raw",
        "output",
        "bbox_padding_degrees",
    }
    _keys(section, allowed, "surface_weather")
    source = _mapping(_required(section, "source", "surface_weather"), "surface_weather.source")
    _keys(
        source,
        {"model", "product", "forecast_hour", "precipitation_forecast_hour"},
        "surface_weather.source",
    )
    if str(source.get("model")) != "hrrr" or str(source.get("product")) != "sfc":
        raise ValueError("surface_weather.source must use HRRR sfc.")
    if int(source.get("forecast_hour", 0)) != 0:
        raise ValueError("The retrospective surface-weather contract requires forecast_hour=0.")
    precipitation_forecast_hour = int(source.get("precipitation_forecast_hour", 1))
    if precipitation_forecast_hour != 1:
        raise ValueError(
            "The six-snapshot precipitation estimate requires precipitation_forecast_hour=1."
        )
    sampling = _mapping(
        _required(section, "sampling", "surface_weather"), "surface_weather.sampling"
    )
    _keys(sampling, {"h3_resolution", "interval_hours"}, "surface_weather.sampling")
    resolution = _validate_resolution(
        _required(sampling, "h3_resolution", "surface_weather.sampling"),
        "surface_weather.sampling.h3_resolution",
    )
    interval = int(_required(sampling, "interval_hours", "surface_weather.sampling"))
    if interval <= 0 or 24 % interval:
        raise ValueError("surface_weather.sampling.interval_hours must divide 24.")

    time = _mapping(_required(section, "time", "surface_weather"), "surface_weather.time")
    _keys(
        time,
        {"start_date", "end_date", "timezone", "availability_lag_hours"},
        "surface_weather.time",
    )
    start = _date(
        _required(time, "start_date", "surface_weather.time"),
        "surface_weather.time.start_date",
    )
    end_raw = str(_required(time, "end_date", "surface_weather.time"))
    end = (
        "latest_complete"
        if end_raw == "latest_complete"
        else _date(end_raw, "surface_weather.time.end_date")
    )
    if end != "latest_complete" and pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError("surface_weather.time.start_date must not follow end_date.")
    timezone = str(_required(time, "timezone", "surface_weather.time"))
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown surface_weather timezone: {timezone}") from exc
    lag = int(time.get("availability_lag_hours", 6))
    if lag < 0:
        raise ValueError("surface_weather.time.availability_lag_hours must be >= 0.")

    raw = _mapping(_required(section, "raw", "surface_weather"), "surface_weather.raw")
    _keys(
        raw,
        {"root_dir", "working_inventory_path", "inventory_path", "manifest_path"},
        "surface_weather.raw",
    )
    output = _mapping(_required(section, "output", "surface_weather"), "surface_weather.output")
    _keys(
        output,
        {"daily_dir", "manifest_path"},
        "surface_weather.output",
    )
    padding = float(section.get("bbox_padding_degrees", 0.2))
    if padding < 0:
        raise ValueError("surface_weather.bbox_padding_degrees must be >= 0.")
    return SurfaceWeatherConfig(
        h3_resolution=resolution,
        interval_hours=interval,
        precipitation_forecast_hour=precipitation_forecast_hour,
        start_date=start,
        end_date=end,
        timezone=timezone,
        availability_lag_hours=lag,
        raw_dir=_path(_required(raw, "root_dir", "surface_weather.raw")),
        working_inventory_path=_path(
            _required(raw, "working_inventory_path", "surface_weather.raw")
        ),
        inventory_path=_path(_required(raw, "inventory_path", "surface_weather.raw")),
        acquisition_manifest_path=_path(_required(raw, "manifest_path", "surface_weather.raw")),
        daily_output_dir=_path(_required(output, "daily_dir", "surface_weather.output")),
        manifest_path=_path(_required(output, "manifest_path", "surface_weather.output")),
        bbox_padding_degrees=padding,
    )


def _load_daylight(section: Mapping[str, Any]) -> DaylightConfig:
    _keys(
        section,
        {
            "h3_resolution",
            "start_date",
            "end_date",
            "timezone",
            "timestep_minutes",
            "low_sun_max_degrees",
            "default_weight",
            "output",
        },
        "daylight",
    )
    output = _mapping(_required(section, "output", "daylight"), "daylight.output")
    _keys(output, {"daily_dir", "day_of_year_path", "manifest_path"}, "daylight.output")
    timestep = int(section.get("timestep_minutes", 30))
    if timestep <= 0 or 60 % timestep:
        raise ValueError("daylight.timestep_minutes must be positive and divide 60.")
    default_weight = str(section.get("default_weight", "fraction"))
    if default_weight not in {"fraction", "cell_norm", "global_norm"}:
        raise ValueError("daylight.default_weight is invalid.")
    timezone = str(section.get("timezone", "America/Los_Angeles"))
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown daylight timezone: {timezone}") from exc
    low_sun = float(section.get("low_sun_max_degrees", 10.0))
    if not 0 < low_sun <= 90:
        raise ValueError("daylight.low_sun_max_degrees must be in (0, 90].")
    return DaylightConfig(
        h3_resolution=_validate_resolution(
            _required(section, "h3_resolution", "daylight"), "daylight.h3_resolution"
        ),
        start_date=_date(_required(section, "start_date", "daylight"), "daylight.start_date"),
        end_date=_date(_required(section, "end_date", "daylight"), "daylight.end_date"),
        timezone=timezone,
        timestep_minutes=timestep,
        low_sun_max_degrees=low_sun,
        default_weight=default_weight,
        daily_output_dir=_path(_required(output, "daily_dir", "daylight.output")),
        day_of_year_output_path=_path(_required(output, "day_of_year_path", "daylight.output")),
        manifest_path=_path(_required(output, "manifest_path", "daylight.output")),
    )


def _load_lunar(section: Mapping[str, Any]) -> LunarConfig:
    _keys(
        section,
        {
            "h3_resolution",
            "start_date",
            "end_date",
            "timezone",
            "timestep_minutes",
            "sample_hour_utc",
            "dark_sun_altitude_deg",
            "moon_altitude_min_deg",
            "output",
        },
        "lunar",
    )
    output = _mapping(_required(section, "output", "lunar"), "lunar.output")
    _keys(output, {"daily_dir", "manifest_path"}, "lunar.output")
    timezone = str(section.get("timezone", "America/Los_Angeles"))
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown lunar timezone: {timezone}") from exc
    timestep = int(section.get("timestep_minutes", 30))
    if timestep <= 0 or 60 % timestep:
        raise ValueError("lunar.timestep_minutes must be positive and divide 60.")
    sample_hour = int(section.get("sample_hour_utc", 12))
    if not 0 <= sample_hour <= 23:
        raise ValueError("lunar.sample_hour_utc must be in [0, 23].")
    dark_sun = float(section.get("dark_sun_altitude_deg", -6.0))
    moon_min = float(section.get("moon_altitude_min_deg", 0.0))
    if not -90 <= dark_sun <= 90:
        raise ValueError("lunar.dark_sun_altitude_deg must be in [-90, 90].")
    if not -90 <= moon_min <= 90:
        raise ValueError("lunar.moon_altitude_min_deg must be in [-90, 90].")
    return LunarConfig(
        h3_resolution=_validate_resolution(
            _required(section, "h3_resolution", "lunar"), "lunar.h3_resolution"
        ),
        start_date=_date(_required(section, "start_date", "lunar"), "lunar.start_date"),
        end_date=_date(_required(section, "end_date", "lunar"), "lunar.end_date"),
        timezone=timezone,
        timestep_minutes=timestep,
        sample_hour_utc=sample_hour,
        dark_sun_altitude_deg=dark_sun,
        moon_altitude_min_deg=moon_min,
        daily_output_dir=_path(_required(output, "daily_dir", "lunar.output")),
        manifest_path=_path(_required(output, "manifest_path", "lunar.output")),
    )


def load_meteorological_config(path: str | Path = DEFAULT_CONFIG_PATH) -> MeteorologicalConfig:
    """Load the canonical meteorological configuration from a domain or project YAML."""

    config_path = resolve_config_path(path)
    raw = load_data_config(config_path, domains="METEOROLOGICAL_LAYER")
    if not isinstance(raw, Mapping):
        raise ValueError("Meteorological configuration must be a mapping.")
    payload = dict(raw)
    _keys(
        payload,
        {"schema_version", "spatial_support", "surface_weather", "daylight", "lunar"},
        "meteorological",
    )
    version = int(payload.get("schema_version", 0))
    if version != 3:
        raise ValueError("Meteorological configuration schema_version must be 3.")

    support = _mapping(_required(payload, "spatial_support", "meteorological"), "spatial_support")
    _keys(support, {"bbox", "resolutions", "output"}, "spatial_support")
    bbox = bbox_from_config(
        _mapping(_required(support, "bbox", "spatial_support"), "spatial_support.bbox"),
        bbox_key="bbox",
    )
    resolutions_raw = _required(support, "resolutions", "spatial_support")
    if not isinstance(resolutions_raw, (list, tuple)) or not resolutions_raw:
        raise ValueError("spatial_support.resolutions must be a non-empty sequence.")
    resolutions = tuple(
        sorted(
            {
                _validate_resolution(value, "spatial_support.resolutions")
                for value in resolutions_raw
            }
        )
    )
    support_output = _mapping(
        _required(support, "output", "spatial_support"), "spatial_support.output"
    )
    _keys(support_output, {"root_dir", "manifest_path"}, "spatial_support.output")

    config = MeteorologicalConfig(
        path=config_path,
        raw=payload,
        schema_version=version,
        bbox={key: float(value) for key, value in bbox.items()},
        support_resolutions=resolutions,
        support_output_dir=_path(_required(support_output, "root_dir", "spatial_support.output")),
        support_manifest_path=_path(
            _required(support_output, "manifest_path", "spatial_support.output")
        ),
        surface_weather=_load_surface(
            _mapping(_required(payload, "surface_weather", "meteorological"), "surface_weather")
        ),
        daylight=_load_daylight(
            _mapping(_required(payload, "daylight", "meteorological"), "daylight")
        ),
        lunar=_load_lunar(_mapping(_required(payload, "lunar", "meteorological"), "lunar")),
    )
    if config.support_resolutions != (4, 5, 6):
        raise ValueError(
            "spatial_support.resolutions must be exactly [4, 5, 6] for the canonical family."
        )
    configured_products = {
        "surface_weather.sampling.h3_resolution": config.surface_weather.h3_resolution,
        "daylight.h3_resolution": config.daylight.h3_resolution,
        "lunar.h3_resolution": config.lunar.h3_resolution,
    }
    expected_products = {
        "surface_weather.sampling.h3_resolution": 5,
        "daylight.h3_resolution": 4,
        "lunar.h3_resolution": 5,
    }
    mismatched = {
        key: value for key, value in configured_products.items() if value != expected_products[key]
    }
    if mismatched:
        raise ValueError(f"Canonical meteorological product resolutions are fixed: {mismatched}.")
    if config.surface_weather.interval_hours != 4:
        raise ValueError(
            "surface_weather.sampling.interval_hours must be 4 for the six-analysis contract."
        )
    if pd.Timestamp(config.daylight.start_date) > pd.Timestamp(config.daylight.end_date):
        raise ValueError("daylight.start_date must not follow daylight.end_date.")
    if pd.Timestamp(config.lunar.start_date) > pd.Timestamp(config.lunar.end_date):
        raise ValueError("lunar.start_date must not follow lunar.end_date.")
    return config
