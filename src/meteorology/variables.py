"""Machine-readable inventory of every published Arrow field.

The schemas determine membership and type. This module owns interpretation;
unknown fields fail catalog construction instead of silently acquiring a
plausible-sounding scientific description.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pyarrow as pa

from .core.data import meteorological_schemas as schemas
from .field_contracts import display_range
from .methods import method_version


PRODUCTS: dict[str, tuple[pa.Schema, str, str]] = {
    "spatial_support": (schemas.SUPPORT_SCHEMA, "meteorological.spatial_support", "R4, R5, R6; timeless"),
    "hrrr_sample": (schemas.HRRR_SAMPLE_SCHEMA, "meteorological.surface_weather.download", "R5; one UTC valid time"),
    "hrrr_inventory": (schemas.HRRR_INVENTORY_SCHEMA, "meteorological.surface_weather.download", "R5; one UTC valid time"),
    "hrrr_crosswalk": (schemas.HRRR_CROSSWALK_SCHEMA, "meteorological.surface_weather.download", "R5; one source grid"),
    "surface_weather": (schemas.SURFACE_WEATHER_DAILY_SCHEMA, "meteorological.surface_weather", "R5; local date"),
    "hourly_weather_inventory": (schemas.HOURLY_WEATHER_INVENTORY_SCHEMA, "meteorological.hourly_weather.acquire", "R5; one UTC f00 valid time"),
    "hourly_weather": (schemas.HOURLY_WEATHER_SCHEMA, "meteorological.hourly_weather", "R5; H3 cell × UTC f00 valid time"),
    "daylight": (schemas.DAYLIGHT_SCHEMA, "meteorological.daylight", "R4; local date"),
    "daylight_day_of_year": (schemas.DAYLIGHT_DOY_SCHEMA, "meteorological.daylight", "R4; leap-year reference lookup with explicit month/day"),
    "weather_summary": (schemas.WEATHER_SUMMARY_SCHEMA, "meteorological.weather_summary", "R5 centroid or region; local day or Monday-start week × metric"),
    "lunar": (schemas.LUNAR_SCHEMA, "meteorological.lunar", "R5; local date"),
}

# name -> source field, unit, processing/aggregation, valid range, interpretation
WEATHER_SAMPLE: dict[str, tuple[str, str, str, str, str]] = {
    "TEMPERATURE_2M_C": ("TMP:2 m above ground", "deg C", "Kelvin - 273.15", "-100..70", "2 m model air temperature"),
    "RELATIVE_HUMIDITY_2M_PCT": ("RH:2 m above ground", "%", "unit-normalized source value", "0..100", "2 m model relative humidity"),
    "U_WIND_10M_MS": ("UGRD:10 m above ground", "m/s", "source value", "finite", "eastward wind component"),
    "V_WIND_10M_MS": ("VGRD:10 m above ground", "m/s", "source value", "finite", "northward wind component"),
    "WIND_SPEED_10M_MS": ("UGRD, VGRD:10 m above ground", "m/s", "hypot(u, v)", "0..inf", "wind speed from vector components"),
    "WIND_GUST_SURFACE_MS": ("GUST:surface", "m/s", "unit-normalized source value", "0..inf", "model surface gust"),
    "VISIBILITY_KM": ("VIS:surface", "km", "metres / 1000", "0..inf", "model horizontal visibility"),
    "TOTAL_CLOUD_COVER_PCT": ("TCDC:entire atmosphere", "%", "unit-normalized source value", "0..100", "total model cloud cover"),
    "PRECIP_RATE_MM_HR": ("PRATE:surface, sfc/f01", "mm/h", "kg m-2 s-1 * 3600", "0..inf", "one-hour-lead forecast precipitation rate at the valid time"),
    "MEAN_SEA_LEVEL_PRESSURE_HPA": ("MSLMA:mean sea level", "hPa", "Pa / 100", "700..1200", "model mean sea-level pressure"),
}

WEATHER_DAILY: dict[str, tuple[str, str, str, str, str]] = {
    "TEMPERATURE_2M_C_MEAN": ("TEMPERATURE_2M_C", "deg C", "arithmetic mean of six samples", "-100..70", "sampled daily mean 2 m temperature"),
    "RELATIVE_HUMIDITY_2M_PCT_MEAN": ("RELATIVE_HUMIDITY_2M_PCT", "%", "arithmetic mean of six samples", "0..100", "sampled daily mean humidity"),
    "WIND_SPEED_10M_MS_MEAN": ("WIND_SPEED_10M_MS", "m/s", "mean of six speed magnitudes", "0..inf", "mean sampled wind speed, distinct from mean-vector speed"),
    "WIND_SPEED_10M_MS_MAX": ("WIND_SPEED_10M_MS", "m/s", "maximum of six speed magnitudes", "0..inf", "maximum sampled wind speed"),
    "U_WIND_10M_MS_MEAN": ("U_WIND_10M_MS", "m/s", "arithmetic mean of six components", "finite", "mean eastward wind component"),
    "V_WIND_10M_MS_MEAN": ("V_WIND_10M_MS", "m/s", "arithmetic mean of six components", "finite", "mean northward wind component"),
    "WIND_VECTOR_SPEED_10M_MS": ("mean U, mean V", "m/s", "hypot(mean u, mean v)", "0..inf", "magnitude of mean wind vector"),
    "WIND_DIRECTION_FROM_10M_DEG": ("mean U, mean V", "degrees clockwise from north", "(degrees(atan2(-mean u, -mean v)) + 360) % 360", "0..<360 or null", "meteorological direction from which the mean vector blows"),
    "WIND_GUST_SURFACE_MS_MEAN": ("WIND_GUST_SURFACE_MS", "m/s", "arithmetic mean of six samples", "0..inf", "mean sampled model gust"),
    "WIND_GUST_SURFACE_MS_MAX": ("WIND_GUST_SURFACE_MS", "m/s", "maximum of six samples", "0..inf", "maximum sampled model gust"),
    "VISIBILITY_KM_MEAN": ("VISIBILITY_KM", "km", "arithmetic mean of six samples", "0..inf", "mean sampled model visibility"),
    "VISIBILITY_KM_MIN": ("VISIBILITY_KM", "km", "minimum of six samples", "0..inf", "minimum sampled model visibility"),
    "TOTAL_CLOUD_COVER_PCT_MEAN": ("TOTAL_CLOUD_COVER_PCT", "%", "arithmetic mean of six samples", "0..100", "mean sampled total model cloud cover"),
    "PRECIP_MM_DAY_ESTIMATE": ("PRECIP_RATE_MM_HR", "mm", "sum of six rates * 4 h", "0..inf", "six-snapshot extrapolation, not a measured 24 h accumulation"),
    "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN": ("MEAN_SEA_LEVEL_PRESSURE_HPA", "hPa", "arithmetic mean of six samples", "700..1200", "mean sampled model sea-level pressure"),
    "MEAN_SEA_LEVEL_PRESSURE_HPA_MIN": ("MEAN_SEA_LEVEL_PRESSURE_HPA", "hPa", "minimum of six samples", "700..1200", "minimum sampled model sea-level pressure"),
}

SOLAR: dict[str, tuple[str, str, str, str, str]] = {
    "DAYLIGHT_HOURS": ("solar declination approximation", "h", "latitude and 365-day solar-day formula", "0..24", "astronomical day length without terrain horizon"),
    "DAYLIGHT_FRACTION": ("DAYLIGHT_HOURS", "fraction", "DAYLIGHT_HOURS / 24", "0..1", "fraction of nominal 24 h day"),
    "DAYLIGHT_WEIGHT_CELL_NORM": ("DAYLIGHT_HOURS", "fraction", "within-cell annual normalization; fraction fallback if annual range is zero", "0..1", "relative daylight weight within a cell"),
    "DAYLIGHT_WEIGHT_GLOBAL_NORM": ("DAYLIGHT_HOURS", "fraction", "global support normalization; fraction fallback if annual range is zero", "0..1", "relative daylight weight across configured support"),
    "DAYLIGHT_WEIGHT": ("configured daylight weight", "fraction", "selected fraction/cell/global normalization", "0..1", "configured daylight weight, not detection probability"),
    "SOLAR_ELEVATION_MAX_DEG": ("approximate solar geometry", "degrees", "maximum of local-day sample altitudes", "-90..90", "sampled maximum solar elevation"),
    "SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG": ("approximate solar geometry", "degrees", "mean positive sampled altitude", "0..90 or null", "sampled mean elevation during daylight; undefined on polar night"),
    "LOW_SUN_DAYLIGHT_HOURS": ("approximate solar geometry", "h", "count of daylight samples under configured low-sun threshold * timestep", "0..25", "low solar-elevation hours"),
    "WEIGHT_DAYLIGHT": ("DAYLIGHT_WEIGHT", "fraction", "selected daily weight on the explicit reference month/day; no extra rescaling", "0..1", "compact reference-calendar daylight weight"),
}

LUNAR: dict[str, tuple[str, str, str, str, str]] = {
    "LUNAR_AGE_DAYS": ("approximate synodic cycle", "days", "elapsed days modulo synodic month at configured UTC hour", "0..29.54", "approximate age since new moon"),
    "LUNAR_PHASE_ANGLE_DEG": ("LUNAR_AGE_DAYS", "degrees", "age / synodic month * 360", "0..<360", "approximate phase angle"),
    "LUNAR_ILLUMINATION_FRACTION": ("LUNAR_PHASE_ANGLE_DEG", "fraction", "(1 - cos(phase angle)) / 2", "0..1", "illuminated lunar disk fraction, not surface moonlight"),
    "MOON_PHASE_NAME": ("LUNAR_AGE_DAYS", "category", "age-bin label", "named phases", "approximate named lunar phase"),
    "NIGHT_HOURS": ("solar altitude", "h", "count of local-day dark samples * timestep", "0..25", "sampled darkness duration; DST days may differ from 24 h"),
    "MOON_VISIBLE_HOURS": ("lunar altitude", "h", "count of above-horizon samples * timestep", "0..25", "sampled geometric moon visibility"),
    "MOON_VISIBLE_DARK_HOURS": ("solar and lunar altitude", "h", "count of dark and moon-above-horizon samples * timestep", "0..25", "sampled geometric moon-visible darkness"),
    "MOONLIT_DARK_HOURS": ("MOON_VISIBLE_DARK_HOURS, lunar illumination", "equivalent h", "sum(illumination fraction * timestep) when dark and moon visible", "0..25", "astronomical exposure proxy, not actual surface illuminance"),
    "MOON_VISIBLE_DARK_FRACTION": ("MOON_VISIBLE_DARK_HOURS, NIGHT_HOURS", "fraction", "visible-dark hours / night hours; null if no night", "0..1 or null", "fraction of night with geometric moon visibility"),
    "MOONLIT_DARK_FRACTION": ("MOONLIT_DARK_HOURS, NIGHT_HOURS", "fraction", "moonlit equivalent hours / night hours; null if no night", "0..1 or null", "astronomical moonlight proxy fraction"),
    "WEIGHT_LUNAR_ILLUMINATION": ("LUNAR_ILLUMINATION_FRACTION", "fraction", "deterministic alias", "0..1", "modeling weight; no observational interpretation"),
    "WEIGHT_MOONLIT_DARK_HOURS": ("MOONLIT_DARK_FRACTION", "fraction", "deterministic alias; null if no night", "0..1 or null", "modeling weight; no observational interpretation"),
}

PHYSICAL = {
    "hrrr_sample": WEATHER_SAMPLE,
    "surface_weather": WEATHER_DAILY,
    "hourly_weather": {key: value for key, value in WEATHER_SAMPLE.items()
                       if key != "PRECIP_RATE_MM_HR"},
    "daylight": SOLAR,
    "daylight_day_of_year": SOLAR,
    "lunar": LUNAR,
}

INVENTORY_NULLS = {
    "SOURCE_URI": "null before or after unsuccessful acquisition",
    "SOURCE_OBJECT_URI": "null before or after unsuccessful acquisition",
    "SOURCE_RETRIEVED_AT_UTC": "null before or after unsuccessful acquisition",
    "RELATIVE_PATH": "null until a validated sample is published",
    "CHECKSUM": "null until a validated sample is published",
    "CROSSWALK_RELATIVE_PATH": "null until a validated crosswalk is published",
    "CROSSWALK_CHECKSUM": "null until a validated crosswalk is published",
    "SOURCE_GRID_HASH": "null when the source grid could not be validated",
    "FAILURE_REASON": "null when acquisition succeeded",
    "PRECIP_INIT_TIME_UTC": "null when precipitation source acquisition failed",
    "PRECIP_FORECAST_HOUR": "null when precipitation source acquisition failed",
    "PRECIP_SOURCE_URI": "null when precipitation source acquisition failed",
    "PRECIP_OBJECT_URI": "null when precipitation source acquisition failed",
    "PRECIP_RETRIEVED_AT_UTC": "null when precipitation source acquisition failed",
}

# Identity, acquisition, calendar and quality fields are documented as
# precisely as the physical variables. Every schema field must resolve here
# or in the family-specific scientific tables above.
METADATA_SOURCE = {
    "H3_INDEX": "H3 cell index at the declared native resolution",
    "H3_RESOLUTION": "H3 resolution of the published cell index",
    "CENTROID_LAT": "H3 WGS84 cell-centroid latitude",
    "CENTROID_LON": "H3 WGS84 cell-centroid longitude",
    "SUPPORT_STATE": "configured atmospheric support membership state",
    "DATE": "configured timezone civil date of the valid time",
    "LOCAL_DATE": "configured timezone civil date of the requested HRRR cycle",
    "VALID_TIME_UTC": "HRRR field valid time in UTC",
    "INIT_TIME_UTC": "HRRR core analysis initialization time in UTC",
    "AVAILABLE_AT_UTC": "declared source availability time in UTC",
    "AVAILABILITY_POLICY": "versioned rule used to derive the declared availability time",
    "SOURCE_GRID_HASH": "checksum of ordered source-grid coordinates",
    "SOURCE_GRID_INDEX": "flattened index of nearest source-grid point",
    "SOURCE_GRID_DISTANCE_M": "great-circle distance from H3 centroid to selected source point",
    "SOURCE_GRID_DISTANCE_M_MEAN": "mean of six nearest-source distances",
    "SOURCE_GRID_DISTANCE_M_MAX": "maximum of six nearest-source distances",
    "SOURCE_MODEL": "selected HRRR model identifier",
    "SOURCE_PRODUCT": "selected HRRR GRIB product identifier",
    "FORECAST_HOUR": "core field forecast lead in hours",
    "SOURCE_DATA_STATE": "validated source-data state",
    "WIND_VECTOR_BASIS": "published 10 m wind components use true east and north axes",
    "SOURCE_WIND_BASIS": "decoded GRIB basis before any required wind rotation",
    "PRECIP_INIT_TIME_UTC": "precipitation forecast initialization time in UTC",
    "PRECIP_VALID_TIME_UTC": "precipitation forecast valid time in UTC",
    "PRECIP_FORECAST_HOUR": "precipitation forecast lead in hours",
    "PRECIP_SOURCE_PRODUCT": "precipitation GRIB product identifier",
    "SOURCE_URI": "actual retrieval URI for the core HRRR fields; may be a supported mirror",
    "SOURCE_OBJECT_URI": "logical NOAA HRRR f00 object identity independent of mirror",
    "SOURCE_RETRIEVED_AT_UTC": "UTC time the core GRIB retrieval completed",
    "SOURCE_EVIDENCE_KIND": "retained decoded HRRR source or explicit synthetic fixture",
    "SAMPLE_CHECKSUM": "SHA-256 checksum of the immutable hourly H3 sample",
    "DECODED_INPUT_CHECKSUM": "SHA-256 checksum of the retained normalized source grid",
    "PRECIP_SOURCE_URI": "actual retrieval URI for the f01 precipitation field",
    "PRECIP_OBJECT_URI": "logical NOAA HRRR f01 object identity independent of mirror",
    "PRECIP_RETRIEVED_AT_UTC": "UTC time the f01 GRIB retrieval completed",
    "RELATIVE_PATH": "sample path relative to the acquisition inventory directory",
    "CHECKSUM": "SHA-256 checksum of the retained sample",
    "CROSSWALK_RELATIVE_PATH": "crosswalk path relative to inventory directory",
    "CROSSWALK_CHECKSUM": "SHA-256 checksum of the retained crosswalk",
    "H3_CELL_COUNT": "count of configured target H3 cells",
    "VARIABLE_COUNT": "count of acquired source variables",
    "STATUS": "per-cycle acquisition completion state",
    "FAILURE_REASON": "recorded cause of an incomplete acquisition cycle",
    "SOURCE_BACKEND": "acquisition backend identifier",
    "SOURCE_FORMAT": "source file encoding identifier",
    "PRECISION_QC_STATE": "source precision and identity quality state",
    "EXPECTED_SAMPLE_COUNT": "configured count of daily source cycles",
    "SAMPLE_COUNT": "count of validated daily source cycles",
    "SAMPLE_COVERAGE_FRAC": "validated cycle count divided by expected count",
    "FIRST_VALID_TIME_UTC": "earliest included core valid time in UTC",
    "LAST_VALID_TIME_UTC": "latest included core valid time in UTC",
    "LATEST_AVAILABLE_AT_UTC": "latest declared availability among included fields",
    "QC_STATE": "daily completeness and quality state",
    "YEAR": "calendar year of configured local DATE",
    "DAY_OF_YEAR": "ordinal local calendar day within year",
    "MONTH": "local calendar month",
    "DAY": "local calendar day of month",
    "MONTH_DAY": "local month and day as MM-DD",
    "IS_LEAP_DAY": "whether local DATE is February 29",
    "SOLAR_DAY_365": "365-day solar proxy index with February 29 mapped to day 60",
}


def _unit(name: str) -> str:
    if name == "H3_INDEX":
        return "H3 cell identifier"
    if name == "H3_RESOLUTION":
        return "H3 resolution level"
    if name in {"DATE", "LOCAL_DATE"}:
        return "ISO 8601 local calendar date"
    if name == "IS_LEAP_DAY":
        return "boolean"
    if name in {"CHECKSUM", "CROSSWALK_CHECKSUM", "SOURCE_GRID_HASH"}:
        return "SHA-256 hex digest"
    if name in {"CENTROID_LAT", "CENTROID_LON", "SOURCE_LAT", "SOURCE_LON"}:
        return "WGS84 degrees"
    if name.endswith("_UTC"):
        return "ISO 8601 UTC timestamp"
    if "_DISTANCE_M" in name:
        return "m"
    if name.endswith("_FRACTION") or name.endswith("_FRAC"):
        return "fraction"
    if name.endswith("_COUNT") or name.endswith("_HOUR") or name.endswith("_INDEX"):
        return "count or identifier"
    return "category or calendar value"


SUMMARY_FIELDS = {
    "PERIOD": ("day or week", "category"),
    "LOCAL_START_DATE": ("first local date of the complete calendar period", "ISO local date"),
    "TIMEZONE": ("configured civil-day timezone", "IANA timezone"),
    "PERIOD_START_UTC": ("inclusive local-midnight period boundary", "UTC timestamp"),
    "PERIOD_END_UTC": ("exclusive local-midnight period boundary", "UTC timestamp"),
    "SPATIAL_SCOPE": ("h3 or region; equal H3 centroid sample weighting", "category"),
    "H3_INDEX": ("native cell identity; null for the explicitly identified region", "H3 cell identifier"),
    "METRIC": ("hourly atmospheric core metric name", "category"),
    "UNIT": ("physical unit of the metric named in this row", "unit label"),
    "SAMPLED_MEAN": ("sum of contributing hourly centroid values divided by VALID_CELL_HOURS", "per UNIT and METRIC"),
    "SAMPLED_MIN": ("minimum contributing hourly centroid value", "per UNIT and METRIC"),
    "SAMPLED_MAX": ("maximum contributing hourly centroid value", "per UNIT and METRIC"),
    "VALID_CELL_HOURS": ("count of contributing cell-hour samples after as-of filtering", "cell-hours"),
    "EXPECTED_CELL_HOURS": ("complete calendar-period UTC hours times target cell count", "cell-hours"),
    "COVERAGE_FRACTION": ("VALID_CELL_HOURS / EXPECTED_CELL_HOURS", "fraction"),
    "STATUS": ("COMPLETE, PARTIAL or UNAVAILABLE from coverage", "category"),
    "AVAILABLE_AT_UTC": ("latest assumed availability of contributing samples; null if unavailable", "UTC timestamp"),
}


def _summary_field(field: pa.Field, support: str) -> dict[str, Any]:
    interpretation, unit = SUMMARY_FIELDS[field.name]
    physical = field.name in {"SAMPLED_MEAN", "SAMPLED_MIN", "SAMPLED_MAX"}
    return {
        "name": field.name, "human_name": field.name.replace("_", " ").title(),
        "source": "validated HRRR f00 hourly releases or explicit synthetic fixtures",
        "source_variable": interpretation, "units": unit,
        "spatial_temporal_support": support, "processing_and_aggregation": interpretation,
        "missing_value_policy": "null H3 identifies region; null statistics and availability mean zero contributing samples; observed zero is preserved",
        "valid_range": "metric-specific physical bounds" if physical else "schema and coverage contract",
        "interpretation": interpretation,
        "limitations": "Retrospective sampled context; regional values are not area averages; edge weeks may be partial",
        "method_version": method_version("meteorological.weather_summary"),
        "output_schema": "weather_summary", "stage": "derived_daily_weekly",
        "predictor_candidate": physical, "variable_kind": "continuous" if physical else "metadata",
        "arrow_type": str(field.type), "nullable": field.nullable,
    }


def catalog() -> dict[str, list[dict[str, Any]]]:
    """Return the complete schema-backed inventory, including identity/QC fields."""

    result: dict[str, list[dict[str, Any]]] = {}
    for family, (schema, product, support) in PRODUCTS.items():
        rows = []
        for field in schema:
            if family == "weather_summary":
                rows.append(_summary_field(field, support))
                continue
            details = PHYSICAL.get(family, {}).get(field.name)
            if details is None:
                if field.name not in METADATA_SOURCE:
                    raise ValueError(f"Missing metadata definition for {family}.{field.name}")
                source, unit, processing, valid_range, interpretation = (
                    METADATA_SOURCE[field.name],
                    _unit(field.name),
                    "copied from validated input or deterministically derived as documented by the producer",
                    "schema and producer validation",
                    METADATA_SOURCE[field.name],
                )
                kind = "metadata"
                predictor = False
                if family == "daylight_day_of_year" and field.name == "DAY_OF_YEAR":
                    source = "ordinal in the year-2000 leap reference calendar"
                    interpretation = "reference ordinal; use MONTH_DAY or SOLAR_DAY_365 for joins across years"
                    valid_range = "1..366 in the leap reference calendar"
            else:
                source, unit, processing, valid_range, interpretation = details
                kind = "circular" if field.name == "WIND_DIRECTION_FROM_10M_DEG" else (
                    "categorical" if field.name == "MOON_PHASE_NAME" else "continuous"
                )
                predictor = field.name not in {"DAYLIGHT_WEIGHT", "WEIGHT_DAYLIGHT", "WEIGHT_LUNAR_ILLUMINATION", "WEIGHT_MOONLIT_DARK_HOURS"}
            shared_range = display_range(field.name)
            valid_range = (
                f"{shared_range} or null" if shared_range and field.nullable else shared_range or valid_range
            )
            rows.append({
                "name": field.name,
                "human_name": field.name.replace("_", " ").title(),
                "source": "NOAA/NCEP HRRR or explicit synthetic fixture" if family.startswith("hourly_weather") else "NOAA/NCEP HRRR" if family.startswith("hrrr") or family == "surface_weather" else "deterministic astronomy" if family in {"daylight", "daylight_day_of_year", "lunar"} else "configured WGS84 bounds and H3",
                "source_variable": source,
                "units": unit,
                "spatial_temporal_support": support,
                "processing_and_aggregation": processing,
                "missing_value_policy": INVENTORY_NULLS[field.name] if family == "hrrr_inventory" and field.name in INVENTORY_NULLS else "null when mean wind vector is calm" if field.name == "WIND_DIRECTION_FROM_10M_DEG" else "null when no sampled daylight" if field.name == "SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG" else "null when NIGHT_HOURS is zero" if field.name in {"MOON_VISIBLE_DARK_FRACTION", "MOONLIT_DARK_FRACTION", "WEIGHT_MOONLIT_DARK_HOURS"} else "reject incomplete source/support; no zero fill" if family in {"surface_weather", "hrrr_sample", "hourly_weather"} else "not applicable to complete deterministic output",
                "valid_range": valid_range,
                "interpretation": interpretation,
                "limitations": "Retrospective model samples; local conditions can differ" if family in {"surface_weather", "hrrr_sample"} else "Approximate geometry; excludes weather and terrain" if family in {"daylight", "daylight_day_of_year", "lunar"} else "Centroid support is not an area average",
                "method_version": method_version(product),
                "output_schema": family,
                "stage": "raw_source_inventory" if family == "hrrr_inventory" else "transformed_sample" if family == "hrrr_sample" else "spatial_mapping" if family in {"hrrr_crosswalk", "spatial_support"} else "astronomical" if family in {"daylight", "daylight_day_of_year", "lunar"} else "derived_daily",
                "predictor_candidate": predictor,
                "variable_kind": kind,
                "arrow_type": str(field.type),
                "nullable": field.nullable,
            })
        result[family] = rows
    return result


def markdown() -> str:
    """Render the inventory from the canonical code-backed catalog."""

    lines = ["# Variable inventory", "", "Generated from `meteorology.variables.catalog()` and the published Arrow schemas.", "Every field has a machine-readable record via `meteorology variables --json`.", "Predictor candidate means only that a field may be considered after availability and leakage review.", ""]
    for family, rows in catalog().items():
        lines.extend([f"## {family.replace('_', ' ').title()}", "", f"Method: `{rows[0]['method_version']}`. Support: {rows[0]['spatial_temporal_support']}.", "", "| Field | Source field | Unit | Processing | Range | Kind | Predictor? |", "| --- | --- | --- | --- | --- | --- | --- |"])
        for row in rows:
            lines.append("| " + " | ".join(str(row[key]).replace("|", "/") for key in ("name", "source_variable", "units", "processing_and_aggregation", "valid_range", "variable_kind", "predictor_candidate")) + " |")
        exceptions = [
            f"`{row['name']}`: {row['missing_value_policy']}"
            for row in rows
            if row["nullable"] or row["missing_value_policy"].startswith("null when")
        ]
        lines.extend([
            "",
            f"Missingness: {rows[0]['missing_value_policy']}. "
            + ("Nullable exceptions: " + "; ".join(exceptions) + ". " if exceptions else "")
            + f"Limitation: {rows[0]['limitations']}.",
            "",
        ])
    return "\n".join(lines).rstrip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", action="store_true", help="Print complete machine-readable metadata.")
    group.add_argument("--markdown", action="store_true", help="Print the documentation table.")
    args = parser.parse_args()
    print(json.dumps(catalog(), indent=2) if args.json else markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
