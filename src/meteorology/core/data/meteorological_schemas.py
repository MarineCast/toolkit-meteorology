"""Canonical Arrow contracts for meteorological datasets.

These contracts live in the neutral data layer so the dataset catalog and the
domain producers share one definition without making ``core`` import a domain.
"""

from __future__ import annotations

import pyarrow as pa

SUPPORT_SCHEMA = pa.schema(
    [
        pa.field("H3_INDEX", pa.string(), nullable=False),
        pa.field("H3_RESOLUTION", pa.int8(), nullable=False),
        pa.field("CENTROID_LAT", pa.float64(), nullable=False),
        pa.field("CENTROID_LON", pa.float64(), nullable=False),
        pa.field("SUPPORT_STATE", pa.string(), nullable=False),
    ]
)

DAYLIGHT_SCHEMA = pa.schema(
    [
        pa.field("H3_INDEX", pa.string(), nullable=False),
        pa.field("DATE", pa.string(), nullable=False),
        pa.field("YEAR", pa.int16(), nullable=False),
        pa.field("DAY_OF_YEAR", pa.int16(), nullable=False),
        pa.field("MONTH", pa.int8(), nullable=False),
        pa.field("DAY", pa.int8(), nullable=False),
        pa.field("MONTH_DAY", pa.string(), nullable=False),
        pa.field("IS_LEAP_DAY", pa.bool_(), nullable=False),
        pa.field("SOLAR_DAY_365", pa.int16(), nullable=False),
        pa.field("CENTROID_LAT", pa.float64(), nullable=False),
        pa.field("CENTROID_LON", pa.float64(), nullable=False),
        pa.field("DAYLIGHT_HOURS", pa.float64(), nullable=False),
        pa.field("DAYLIGHT_FRACTION", pa.float64(), nullable=False),
        pa.field("DAYLIGHT_WEIGHT_CELL_NORM", pa.float64(), nullable=False),
        pa.field("DAYLIGHT_WEIGHT_GLOBAL_NORM", pa.float64(), nullable=False),
        pa.field("DAYLIGHT_WEIGHT", pa.float64(), nullable=False),
        pa.field("SOLAR_ELEVATION_MAX_DEG", pa.float64(), nullable=False),
        pa.field("SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG", pa.float64(), nullable=False),
        pa.field("LOW_SUN_DAYLIGHT_HOURS", pa.float64(), nullable=False),
    ]
)

DAYLIGHT_DOY_SCHEMA = pa.schema(
    [
        pa.field("H3_INDEX", pa.string(), nullable=False),
        pa.field("DAY_OF_YEAR", pa.int16(), nullable=False),
        pa.field("WEIGHT_DAYLIGHT", pa.float64(), nullable=False),
    ]
)

LUNAR_SCHEMA = pa.schema(
    [
        pa.field("H3_INDEX", pa.string(), nullable=False),
        pa.field("DATE", pa.string(), nullable=False),
        pa.field("YEAR", pa.int16(), nullable=False),
        pa.field("DAY_OF_YEAR", pa.int16(), nullable=False),
        pa.field("MONTH", pa.int8(), nullable=False),
        pa.field("DAY", pa.int8(), nullable=False),
        pa.field("MONTH_DAY", pa.string(), nullable=False),
        pa.field("IS_LEAP_DAY", pa.bool_(), nullable=False),
        pa.field("CENTROID_LAT", pa.float64(), nullable=False),
        pa.field("CENTROID_LON", pa.float64(), nullable=False),
        pa.field("LUNAR_AGE_DAYS", pa.float64(), nullable=False),
        pa.field("LUNAR_PHASE_ANGLE_DEG", pa.float64(), nullable=False),
        pa.field("LUNAR_ILLUMINATION_FRACTION", pa.float64(), nullable=False),
        pa.field("MOON_PHASE_NAME", pa.string(), nullable=False),
        pa.field("NIGHT_HOURS", pa.float64(), nullable=False),
        pa.field("MOON_VISIBLE_HOURS", pa.float64(), nullable=False),
        pa.field("MOON_VISIBLE_DARK_HOURS", pa.float64(), nullable=False),
        pa.field("MOONLIT_DARK_HOURS", pa.float64(), nullable=False),
        pa.field("MOON_VISIBLE_DARK_FRACTION", pa.float64(), nullable=False),
        pa.field("MOONLIT_DARK_FRACTION", pa.float64(), nullable=False),
        pa.field("WEIGHT_LUNAR_ILLUMINATION", pa.float64(), nullable=False),
        pa.field("WEIGHT_MOONLIT_DARK_HOURS", pa.float64(), nullable=False),
    ]
)

HRRR_CROSSWALK_SCHEMA = pa.schema(
    [
        pa.field("H3_INDEX", pa.string(), nullable=False),
        pa.field("SOURCE_GRID_HASH", pa.string(), nullable=False),
        pa.field("SOURCE_GRID_INDEX", pa.int32(), nullable=False),
        pa.field("SOURCE_GRID_DISTANCE_M", pa.float64(), nullable=False),
    ]
)

PRE_F01_SAMPLE_SCHEMA = pa.schema(
    [
        pa.field("H3_INDEX", pa.string(), nullable=False),
        pa.field("DATE", pa.string(), nullable=False),
        pa.field("VALID_TIME_UTC", pa.string(), nullable=False),
        pa.field("INIT_TIME_UTC", pa.string(), nullable=False),
        pa.field("AVAILABLE_AT_UTC", pa.string(), nullable=False),
        pa.field("TEMPERATURE_2M_C", pa.float64(), nullable=False),
        pa.field("RELATIVE_HUMIDITY_2M_PCT", pa.float64(), nullable=False),
        pa.field("U_WIND_10M_MS", pa.float64(), nullable=False),
        pa.field("V_WIND_10M_MS", pa.float64(), nullable=False),
        pa.field("WIND_SPEED_10M_MS", pa.float64(), nullable=False),
        pa.field("WIND_GUST_SURFACE_MS", pa.float64(), nullable=False),
        pa.field("VISIBILITY_KM", pa.float64(), nullable=False),
        pa.field("TOTAL_CLOUD_COVER_PCT", pa.float64(), nullable=False),
        pa.field("PRECIP_RATE_MM_HR", pa.float64(), nullable=False),
        pa.field("MEAN_SEA_LEVEL_PRESSURE_HPA", pa.float64(), nullable=False),
        pa.field("SOURCE_GRID_HASH", pa.string(), nullable=False),
        pa.field("SOURCE_GRID_INDEX", pa.int32(), nullable=False),
        pa.field("SOURCE_GRID_DISTANCE_M", pa.float64(), nullable=False),
        pa.field("SOURCE_MODEL", pa.string(), nullable=False),
        pa.field("SOURCE_PRODUCT", pa.string(), nullable=False),
        pa.field("FORECAST_HOUR", pa.int16(), nullable=False),
        pa.field("SOURCE_DATA_STATE", pa.string(), nullable=False),
    ]
)

HRRR_SAMPLE_SCHEMA = pa.schema(
    [
        *PRE_F01_SAMPLE_SCHEMA,
        pa.field("PRECIP_INIT_TIME_UTC", pa.string(), nullable=False),
        pa.field("PRECIP_VALID_TIME_UTC", pa.string(), nullable=False),
        pa.field("PRECIP_FORECAST_HOUR", pa.int16(), nullable=False),
        pa.field("PRECIP_SOURCE_PRODUCT", pa.string(), nullable=False),
    ]
)

PRE_F01_INVENTORY_SCHEMA = pa.schema(
    [
        pa.field("LOCAL_DATE", pa.string(), nullable=False),
        pa.field("VALID_TIME_UTC", pa.string(), nullable=False),
        pa.field("INIT_TIME_UTC", pa.string(), nullable=False),
        pa.field("AVAILABLE_AT_UTC", pa.string(), nullable=False),
        pa.field("SOURCE_URI", pa.string(), nullable=True),
        pa.field("RELATIVE_PATH", pa.string(), nullable=True),
        pa.field("CHECKSUM", pa.string(), nullable=True),
        pa.field("CROSSWALK_RELATIVE_PATH", pa.string(), nullable=True),
        pa.field("CROSSWALK_CHECKSUM", pa.string(), nullable=True),
        pa.field("SOURCE_GRID_HASH", pa.string(), nullable=True),
        pa.field("H3_RESOLUTION", pa.int8(), nullable=False),
        pa.field("H3_CELL_COUNT", pa.int32(), nullable=False),
        pa.field("VARIABLE_COUNT", pa.int16(), nullable=False),
        pa.field("STATUS", pa.string(), nullable=False),
        pa.field("FAILURE_REASON", pa.string(), nullable=True),
        pa.field("SOURCE_BACKEND", pa.string(), nullable=False),
        pa.field("SOURCE_FORMAT", pa.string(), nullable=False),
        pa.field("PRECISION_QC_STATE", pa.string(), nullable=False),
    ]
)

HRRR_INVENTORY_SCHEMA = pa.schema(
    [
        *PRE_F01_INVENTORY_SCHEMA,
        pa.field("PRECIP_INIT_TIME_UTC", pa.string(), nullable=True),
        pa.field("PRECIP_FORECAST_HOUR", pa.int16(), nullable=True),
        pa.field("PRECIP_SOURCE_URI", pa.string(), nullable=True),
    ]
)

SURFACE_WEATHER_DAILY_SCHEMA = pa.schema(
    [
        pa.field("H3_INDEX", pa.string(), nullable=False),
        pa.field("DATE", pa.string(), nullable=False),
        pa.field("TEMPERATURE_2M_C_MEAN", pa.float64(), nullable=False),
        pa.field("RELATIVE_HUMIDITY_2M_PCT_MEAN", pa.float64(), nullable=False),
        pa.field("WIND_SPEED_10M_MS_MEAN", pa.float64(), nullable=False),
        pa.field("WIND_SPEED_10M_MS_MAX", pa.float64(), nullable=False),
        pa.field("WIND_GUST_SURFACE_MS_MEAN", pa.float64(), nullable=False),
        pa.field("WIND_GUST_SURFACE_MS_MAX", pa.float64(), nullable=False),
        pa.field("VISIBILITY_KM_MEAN", pa.float64(), nullable=False),
        pa.field("VISIBILITY_KM_MIN", pa.float64(), nullable=False),
        pa.field("TOTAL_CLOUD_COVER_PCT_MEAN", pa.float64(), nullable=False),
        pa.field("PRECIP_MM_DAY_ESTIMATE", pa.float64(), nullable=False),
        pa.field("MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN", pa.float64(), nullable=False),
        pa.field("MEAN_SEA_LEVEL_PRESSURE_HPA_MIN", pa.float64(), nullable=False),
        pa.field("EXPECTED_SAMPLE_COUNT", pa.int16(), nullable=False),
        pa.field("SAMPLE_COUNT", pa.int16(), nullable=False),
        pa.field("SAMPLE_COVERAGE_FRAC", pa.float64(), nullable=False),
        pa.field("FIRST_VALID_TIME_UTC", pa.string(), nullable=False),
        pa.field("LAST_VALID_TIME_UTC", pa.string(), nullable=False),
        pa.field("LATEST_AVAILABLE_AT_UTC", pa.string(), nullable=False),
        pa.field("SOURCE_GRID_DISTANCE_M_MEAN", pa.float64(), nullable=False),
        pa.field("SOURCE_GRID_DISTANCE_M_MAX", pa.float64(), nullable=False),
        pa.field("QC_STATE", pa.string(), nullable=False),
    ]
)
