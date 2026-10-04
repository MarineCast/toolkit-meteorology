# Variable inventory

Generated from `meteorology.variables.catalog()` and the published Arrow schemas.
Every field has a machine-readable record via `meteorology variables --json`.
Predictor candidate means only that a field may be considered after availability and leakage review.

## Spatial Support

Method: `h3_bbox_centroid_support_v2`. Support: R4, R5, R6; timeless.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| H3_INDEX | H3 cell index at the declared native resolution | H3 cell identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| H3_RESOLUTION | H3 resolution of the published cell index | H3 resolution level | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| CENTROID_LAT | H3 WGS84 cell-centroid latitude | WGS84 degrees | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| CENTROID_LON | H3 WGS84 cell-centroid longitude | WGS84 degrees | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SUPPORT_STATE | configured atmospheric support membership state | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |

Missingness: not applicable to complete deterministic output. Limitation: Centroid support is not an area average.

## Hrrr Sample

Method: `hrrr_f00_f01_earth_wind_sample_v4`. Support: R5; one UTC valid time.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| H3_INDEX | H3 cell index at the declared native resolution | H3 cell identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DATE | configured timezone civil date of the valid time | ISO 8601 local calendar date | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| VALID_TIME_UTC | HRRR field valid time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| INIT_TIME_UTC | HRRR core analysis initialization time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| AVAILABLE_AT_UTC | declared source availability time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| AVAILABILITY_POLICY | versioned rule used to derive the declared availability time | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| TEMPERATURE_2M_C | TMP:2 m above ground | deg C | Kelvin - 273.15 | -100..70 | continuous | True |
| RELATIVE_HUMIDITY_2M_PCT | RH:2 m above ground | % | unit-normalized source value | 0..100 | continuous | True |
| U_WIND_10M_MS | UGRD:10 m above ground | m/s | source value | finite | continuous | True |
| V_WIND_10M_MS | VGRD:10 m above ground | m/s | source value | finite | continuous | True |
| WIND_SPEED_10M_MS | UGRD, VGRD:10 m above ground | m/s | hypot(u, v) | 0..inf | continuous | True |
| WIND_VECTOR_BASIS | published 10 m wind components use true east and north axes | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_WIND_BASIS | decoded GRIB basis before any required wind rotation | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| WIND_GUST_SURFACE_MS | GUST:surface | m/s | unit-normalized source value | 0..inf | continuous | True |
| VISIBILITY_KM | VIS:surface | km | metres / 1000 | 0..inf | continuous | True |
| TOTAL_CLOUD_COVER_PCT | TCDC:entire atmosphere | % | unit-normalized source value | 0..100 | continuous | True |
| PRECIP_RATE_MM_HR | PRATE:surface, sfc/f01 | mm/h | kg m-2 s-1 * 3600 | 0..inf | continuous | True |
| MEAN_SEA_LEVEL_PRESSURE_HPA | MSLMA:mean sea level | hPa | Pa / 100 | 700..1200 | continuous | True |
| SOURCE_GRID_HASH | checksum of ordered source-grid coordinates | SHA-256 hex digest | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_GRID_INDEX | flattened index of nearest source-grid point | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_GRID_DISTANCE_M | great-circle distance from H3 centroid to selected source point | m | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_MODEL | selected HRRR model identifier | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_PRODUCT | selected HRRR GRIB product identifier | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| FORECAST_HOUR | core field forecast lead in hours | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_DATA_STATE | validated source-data state | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| PRECIP_INIT_TIME_UTC | precipitation forecast initialization time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| PRECIP_VALID_TIME_UTC | precipitation forecast valid time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| PRECIP_FORECAST_HOUR | precipitation forecast lead in hours | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| PRECIP_SOURCE_PRODUCT | precipitation GRIB product identifier | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |

Missingness: reject incomplete source/support; no zero fill. Limitation: Retrospective model samples; local conditions can differ.

## Hrrr Inventory

Method: `hrrr_f00_f01_earth_wind_sample_v4`. Support: R5; one UTC valid time.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| LOCAL_DATE | configured timezone civil date of the requested HRRR cycle | ISO 8601 local calendar date | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| VALID_TIME_UTC | HRRR field valid time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| INIT_TIME_UTC | HRRR core analysis initialization time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| AVAILABLE_AT_UTC | declared source availability time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_URI | actual retrieval URI for the core HRRR fields; may be a supported mirror | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| RELATIVE_PATH | sample path relative to the acquisition inventory directory | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| CHECKSUM | SHA-256 checksum of the retained sample | SHA-256 hex digest | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| CROSSWALK_RELATIVE_PATH | crosswalk path relative to inventory directory | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| CROSSWALK_CHECKSUM | SHA-256 checksum of the retained crosswalk | SHA-256 hex digest | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_GRID_HASH | checksum of ordered source-grid coordinates | SHA-256 hex digest | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| H3_RESOLUTION | H3 resolution of the published cell index | H3 resolution level | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| H3_CELL_COUNT | count of configured target H3 cells | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| VARIABLE_COUNT | count of acquired source variables | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| STATUS | per-cycle acquisition completion state | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| FAILURE_REASON | recorded cause of an incomplete acquisition cycle | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_BACKEND | acquisition backend identifier | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_FORMAT | source file encoding identifier | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| PRECISION_QC_STATE | source precision and identity quality state | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| PRECIP_INIT_TIME_UTC | precipitation forecast initialization time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| PRECIP_FORECAST_HOUR | precipitation forecast lead in hours | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| PRECIP_SOURCE_URI | actual retrieval URI for the f01 precipitation field | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_OBJECT_URI | logical NOAA HRRR f00 object identity independent of mirror | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_RETRIEVED_AT_UTC | UTC time the core GRIB retrieval completed | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| PRECIP_OBJECT_URI | logical NOAA HRRR f01 object identity independent of mirror | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| PRECIP_RETRIEVED_AT_UTC | UTC time the f01 GRIB retrieval completed | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |

Missingness: not applicable to complete deterministic output. Nullable exceptions: `SOURCE_URI`: null before or after unsuccessful acquisition; `RELATIVE_PATH`: null until a validated sample is published; `CHECKSUM`: null until a validated sample is published; `CROSSWALK_RELATIVE_PATH`: null until a validated crosswalk is published; `CROSSWALK_CHECKSUM`: null until a validated crosswalk is published; `SOURCE_GRID_HASH`: null when the source grid could not be validated; `FAILURE_REASON`: null when acquisition succeeded; `PRECIP_INIT_TIME_UTC`: null when precipitation source acquisition failed; `PRECIP_FORECAST_HOUR`: null when precipitation source acquisition failed; `PRECIP_SOURCE_URI`: null when precipitation source acquisition failed; `SOURCE_OBJECT_URI`: null before or after unsuccessful acquisition; `SOURCE_RETRIEVED_AT_UTC`: null before or after unsuccessful acquisition; `PRECIP_OBJECT_URI`: null when precipitation source acquisition failed; `PRECIP_RETRIEVED_AT_UTC`: null when precipitation source acquisition failed. Limitation: Centroid support is not an area average.

## Hrrr Crosswalk

Method: `hrrr_f00_f01_earth_wind_sample_v4`. Support: R5; one source grid.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| H3_INDEX | H3 cell index at the declared native resolution | H3 cell identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_GRID_HASH | checksum of ordered source-grid coordinates | SHA-256 hex digest | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_GRID_INDEX | flattened index of nearest source-grid point | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_GRID_DISTANCE_M | great-circle distance from H3 centroid to selected source point | m | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |

Missingness: not applicable to complete deterministic output. Limitation: Centroid support is not an area average.

## Surface Weather

Method: `hrrr_surface_daily_earth_wind_v5`. Support: R5; local date.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| H3_INDEX | H3 cell index at the declared native resolution | H3 cell identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DATE | configured timezone civil date of the valid time | ISO 8601 local calendar date | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| TEMPERATURE_2M_C_MEAN | TEMPERATURE_2M_C | deg C | arithmetic mean of six samples | -100..70 | continuous | True |
| RELATIVE_HUMIDITY_2M_PCT_MEAN | RELATIVE_HUMIDITY_2M_PCT | % | arithmetic mean of six samples | 0..100 | continuous | True |
| WIND_SPEED_10M_MS_MEAN | WIND_SPEED_10M_MS | m/s | mean of six speed magnitudes | 0..inf | continuous | True |
| WIND_SPEED_10M_MS_MAX | WIND_SPEED_10M_MS | m/s | maximum of six speed magnitudes | 0..inf | continuous | True |
| U_WIND_10M_MS_MEAN | U_WIND_10M_MS | m/s | arithmetic mean of six components | finite | continuous | True |
| V_WIND_10M_MS_MEAN | V_WIND_10M_MS | m/s | arithmetic mean of six components | finite | continuous | True |
| WIND_VECTOR_SPEED_10M_MS | mean U, mean V | m/s | hypot(mean u, mean v) | 0..inf | continuous | True |
| WIND_DIRECTION_FROM_10M_DEG | mean U, mean V | degrees clockwise from north | (degrees(atan2(-mean u, -mean v)) + 360) % 360 | 0..<360 or null | circular | True |
| WIND_GUST_SURFACE_MS_MEAN | WIND_GUST_SURFACE_MS | m/s | arithmetic mean of six samples | 0..inf | continuous | True |
| WIND_GUST_SURFACE_MS_MAX | WIND_GUST_SURFACE_MS | m/s | maximum of six samples | 0..inf | continuous | True |
| VISIBILITY_KM_MEAN | VISIBILITY_KM | km | arithmetic mean of six samples | 0..inf | continuous | True |
| VISIBILITY_KM_MIN | VISIBILITY_KM | km | minimum of six samples | 0..inf | continuous | True |
| TOTAL_CLOUD_COVER_PCT_MEAN | TOTAL_CLOUD_COVER_PCT | % | arithmetic mean of six samples | 0..100 | continuous | True |
| PRECIP_MM_DAY_ESTIMATE | PRECIP_RATE_MM_HR | mm | sum of six rates * 4 h | 0..inf | continuous | True |
| MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN | MEAN_SEA_LEVEL_PRESSURE_HPA | hPa | arithmetic mean of six samples | 700..1200 | continuous | True |
| MEAN_SEA_LEVEL_PRESSURE_HPA_MIN | MEAN_SEA_LEVEL_PRESSURE_HPA | hPa | minimum of six samples | 700..1200 | continuous | True |
| EXPECTED_SAMPLE_COUNT | configured count of daily source cycles | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SAMPLE_COUNT | count of validated daily source cycles | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SAMPLE_COVERAGE_FRAC | validated cycle count divided by expected count | fraction | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| FIRST_VALID_TIME_UTC | earliest included core valid time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| LAST_VALID_TIME_UTC | latest included core valid time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| LATEST_AVAILABLE_AT_UTC | latest declared availability among included fields | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_GRID_DISTANCE_M_MEAN | mean of six nearest-source distances | m | copied from validated input or deterministically derived as documented by the producer | 0..inf | metadata | False |
| SOURCE_GRID_DISTANCE_M_MAX | maximum of six nearest-source distances | m | copied from validated input or deterministically derived as documented by the producer | 0..inf | metadata | False |
| QC_STATE | daily completeness and quality state | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |

Missingness: reject incomplete source/support; no zero fill. Nullable exceptions: `WIND_DIRECTION_FROM_10M_DEG`: null when mean wind vector is calm. Limitation: Retrospective model samples; local conditions can differ.

## Hourly Weather Inventory

Method: `hrrr_f00_hourly_source_r5_v1`. Support: R5; one UTC f00 valid time.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| LOCAL_DATE | configured timezone civil date of the requested HRRR cycle | ISO 8601 local calendar date | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| VALID_TIME_UTC | HRRR field valid time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| INIT_TIME_UTC | HRRR core analysis initialization time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| AVAILABLE_AT_UTC | declared source availability time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SAMPLE_CHECKSUM | SHA-256 checksum of the immutable hourly H3 sample | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| CROSSWALK_CHECKSUM | SHA-256 checksum of the retained crosswalk | SHA-256 hex digest | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_GRID_HASH | checksum of ordered source-grid coordinates | SHA-256 hex digest | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| H3_CELL_COUNT | count of configured target H3 cells | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_OBJECT_URI | logical NOAA HRRR f00 object identity independent of mirror | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_URI | actual retrieval URI for the core HRRR fields; may be a supported mirror | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_RETRIEVED_AT_UTC | UTC time the core GRIB retrieval completed | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_EVIDENCE_KIND | retained decoded HRRR source or explicit synthetic fixture | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DECODED_INPUT_CHECKSUM | SHA-256 checksum of the retained normalized source grid | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |

Missingness: not applicable to complete deterministic output. Limitation: Centroid support is not an area average.

## Hourly Weather

Method: `hrrr_f00_hourly_h3_r5_v1`. Support: R5; H3 cell × UTC f00 valid time.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| H3_INDEX | H3 cell index at the declared native resolution | H3 cell identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DATE | configured timezone civil date of the valid time | ISO 8601 local calendar date | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| VALID_TIME_UTC | HRRR field valid time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| INIT_TIME_UTC | HRRR core analysis initialization time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| AVAILABLE_AT_UTC | declared source availability time in UTC | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| AVAILABILITY_POLICY | versioned rule used to derive the declared availability time | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| TEMPERATURE_2M_C | TMP:2 m above ground | deg C | Kelvin - 273.15 | -100..70 | continuous | True |
| RELATIVE_HUMIDITY_2M_PCT | RH:2 m above ground | % | unit-normalized source value | 0..100 | continuous | True |
| U_WIND_10M_MS | UGRD:10 m above ground | m/s | source value | finite | continuous | True |
| V_WIND_10M_MS | VGRD:10 m above ground | m/s | source value | finite | continuous | True |
| WIND_SPEED_10M_MS | UGRD, VGRD:10 m above ground | m/s | hypot(u, v) | 0..inf | continuous | True |
| WIND_VECTOR_BASIS | published 10 m wind components use true east and north axes | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_WIND_BASIS | decoded GRIB basis before any required wind rotation | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| WIND_GUST_SURFACE_MS | GUST:surface | m/s | unit-normalized source value | 0..inf | continuous | True |
| VISIBILITY_KM | VIS:surface | km | metres / 1000 | 0..inf | continuous | True |
| TOTAL_CLOUD_COVER_PCT | TCDC:entire atmosphere | % | unit-normalized source value | 0..100 | continuous | True |
| MEAN_SEA_LEVEL_PRESSURE_HPA | MSLMA:mean sea level | hPa | Pa / 100 | 700..1200 | continuous | True |
| SOURCE_GRID_HASH | checksum of ordered source-grid coordinates | SHA-256 hex digest | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_GRID_INDEX | flattened index of nearest source-grid point | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_GRID_DISTANCE_M | great-circle distance from H3 centroid to selected source point | m | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_MODEL | selected HRRR model identifier | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_PRODUCT | selected HRRR GRIB product identifier | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| FORECAST_HOUR | core field forecast lead in hours | count or identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_DATA_STATE | validated source-data state | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_OBJECT_URI | logical NOAA HRRR f00 object identity independent of mirror | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_URI | actual retrieval URI for the core HRRR fields; may be a supported mirror | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_RETRIEVED_AT_UTC | UTC time the core GRIB retrieval completed | ISO 8601 UTC timestamp | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOURCE_EVIDENCE_KIND | retained decoded HRRR source or explicit synthetic fixture | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |

Missingness: reject incomplete source/support; no zero fill. Limitation: Centroid support is not an area average.

## Daylight

Method: `daylight_astronomy_v3`. Support: R4; local date.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| H3_INDEX | H3 cell index at the declared native resolution | H3 cell identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DATE | configured timezone civil date of the valid time | ISO 8601 local calendar date | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| YEAR | calendar year of configured local DATE | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DAY_OF_YEAR | ordinal local calendar day within year | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| MONTH | local calendar month | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DAY | local calendar day of month | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| MONTH_DAY | local month and day as MM-DD | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| IS_LEAP_DAY | whether local DATE is February 29 | boolean | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOLAR_DAY_365 | 365-day solar proxy index with February 29 mapped to day 60 | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| CENTROID_LAT | H3 WGS84 cell-centroid latitude | WGS84 degrees | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| CENTROID_LON | H3 WGS84 cell-centroid longitude | WGS84 degrees | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DAYLIGHT_HOURS | solar declination approximation | h | latitude and 365-day solar-day formula | 0..24 | continuous | True |
| DAYLIGHT_FRACTION | DAYLIGHT_HOURS | fraction | DAYLIGHT_HOURS / 24 | 0..1 | continuous | True |
| DAYLIGHT_WEIGHT_CELL_NORM | DAYLIGHT_HOURS | fraction | within-cell annual normalization; fraction fallback if annual range is zero | 0..1 | continuous | True |
| DAYLIGHT_WEIGHT_GLOBAL_NORM | DAYLIGHT_HOURS | fraction | global support normalization; fraction fallback if annual range is zero | 0..1 | continuous | True |
| DAYLIGHT_WEIGHT | configured daylight weight | fraction | selected fraction/cell/global normalization | 0..1 | continuous | False |
| SOLAR_ELEVATION_MAX_DEG | approximate solar geometry | degrees | maximum of local-day sample altitudes | -90..90 | continuous | True |
| SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG | approximate solar geometry | degrees | mean positive sampled altitude | 0..90 or null | continuous | True |
| LOW_SUN_DAYLIGHT_HOURS | approximate solar geometry | h | count of daylight samples under configured low-sun threshold * timestep | 0..local civil-day hours (23/24/25) | continuous | True |

Missingness: not applicable to complete deterministic output. Nullable exceptions: `SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG`: null when no sampled daylight. Limitation: Approximate geometry; excludes weather and terrain.

## Daylight Day Of Year

Method: `daylight_astronomy_v3`. Support: R4; leap-year reference lookup with explicit month/day.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| H3_INDEX | H3 cell index at the declared native resolution | H3 cell identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DAY_OF_YEAR | ordinal in the year-2000 leap reference calendar | category or calendar value | copied from validated input or deterministically derived as documented by the producer | 1..366 in the leap reference calendar | metadata | False |
| MONTH_DAY | local month and day as MM-DD | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| IS_LEAP_DAY | whether local DATE is February 29 | boolean | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| SOLAR_DAY_365 | 365-day solar proxy index with February 29 mapped to day 60 | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| WEIGHT_DAYLIGHT | DAYLIGHT_WEIGHT | fraction | selected daily weight on the explicit reference month/day; no extra rescaling | 0..1 | continuous | False |

Missingness: not applicable to complete deterministic output. Limitation: Approximate geometry; excludes weather and terrain.

## Weather Summary

Method: `hourly_sample_daily_weekly_summary_v1`. Support: R5 centroid or region; local day or Monday-start week × metric.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| PERIOD | day or week | category | day or week | schema and coverage contract | metadata | False |
| LOCAL_START_DATE | first local date of the complete calendar period | ISO local date | first local date of the complete calendar period | schema and coverage contract | metadata | False |
| TIMEZONE | configured civil-day timezone | IANA timezone | configured civil-day timezone | schema and coverage contract | metadata | False |
| PERIOD_START_UTC | inclusive local-midnight period boundary | UTC timestamp | inclusive local-midnight period boundary | schema and coverage contract | metadata | False |
| PERIOD_END_UTC | exclusive local-midnight period boundary | UTC timestamp | exclusive local-midnight period boundary | schema and coverage contract | metadata | False |
| SPATIAL_SCOPE | h3 or region; equal H3 centroid sample weighting | category | h3 or region; equal H3 centroid sample weighting | schema and coverage contract | metadata | False |
| H3_INDEX | native cell identity; null for the explicitly identified region | H3 cell identifier | native cell identity; null for the explicitly identified region | schema and coverage contract | metadata | False |
| METRIC | hourly atmospheric core metric name | category | hourly atmospheric core metric name | schema and coverage contract | metadata | False |
| UNIT | physical unit of the metric named in this row | unit label | physical unit of the metric named in this row | schema and coverage contract | metadata | False |
| SAMPLED_MEAN | sum of contributing hourly centroid values divided by VALID_CELL_HOURS | per UNIT and METRIC | sum of contributing hourly centroid values divided by VALID_CELL_HOURS | metric-specific physical bounds | continuous | True |
| SAMPLED_MIN | minimum contributing hourly centroid value | per UNIT and METRIC | minimum contributing hourly centroid value | metric-specific physical bounds | continuous | True |
| SAMPLED_MAX | maximum contributing hourly centroid value | per UNIT and METRIC | maximum contributing hourly centroid value | metric-specific physical bounds | continuous | True |
| VALID_CELL_HOURS | count of contributing cell-hour samples after as-of filtering | cell-hours | count of contributing cell-hour samples after as-of filtering | schema and coverage contract | metadata | False |
| EXPECTED_CELL_HOURS | complete calendar-period UTC hours times target cell count | cell-hours | complete calendar-period UTC hours times target cell count | schema and coverage contract | metadata | False |
| COVERAGE_FRACTION | VALID_CELL_HOURS / EXPECTED_CELL_HOURS | fraction | VALID_CELL_HOURS / EXPECTED_CELL_HOURS | schema and coverage contract | metadata | False |
| STATUS | COMPLETE, PARTIAL or UNAVAILABLE from coverage | category | COMPLETE, PARTIAL or UNAVAILABLE from coverage | schema and coverage contract | metadata | False |
| AVAILABLE_AT_UTC | latest assumed availability of contributing samples; null if unavailable | UTC timestamp | latest assumed availability of contributing samples; null if unavailable | schema and coverage contract | metadata | False |

Missingness: null H3 identifies region; null statistics and availability mean zero contributing samples; observed zero is preserved. Nullable exceptions: `H3_INDEX`: null H3 identifies region; null statistics and availability mean zero contributing samples; observed zero is preserved; `SAMPLED_MEAN`: null H3 identifies region; null statistics and availability mean zero contributing samples; observed zero is preserved; `SAMPLED_MIN`: null H3 identifies region; null statistics and availability mean zero contributing samples; observed zero is preserved; `SAMPLED_MAX`: null H3 identifies region; null statistics and availability mean zero contributing samples; observed zero is preserved; `AVAILABLE_AT_UTC`: null H3 identifies region; null statistics and availability mean zero contributing samples; observed zero is preserved. Limitation: Retrospective sampled context; regional values are not area averages; edge weeks may be partial.

## Lunar

Method: `lunar_illumination_v2`. Support: R5; local date.

| Field | Source field | Unit | Processing | Range | Kind | Predictor? |
| --- | --- | --- | --- | --- | --- | --- |
| H3_INDEX | H3 cell index at the declared native resolution | H3 cell identifier | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DATE | configured timezone civil date of the valid time | ISO 8601 local calendar date | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| YEAR | calendar year of configured local DATE | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DAY_OF_YEAR | ordinal local calendar day within year | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| MONTH | local calendar month | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| DAY | local calendar day of month | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| MONTH_DAY | local month and day as MM-DD | category or calendar value | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| IS_LEAP_DAY | whether local DATE is February 29 | boolean | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| CENTROID_LAT | H3 WGS84 cell-centroid latitude | WGS84 degrees | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| CENTROID_LON | H3 WGS84 cell-centroid longitude | WGS84 degrees | copied from validated input or deterministically derived as documented by the producer | schema and producer validation | metadata | False |
| LUNAR_AGE_DAYS | approximate synodic cycle | days | elapsed days modulo synodic month at configured UTC hour | 0..29.54 | continuous | True |
| LUNAR_PHASE_ANGLE_DEG | LUNAR_AGE_DAYS | degrees | age / synodic month * 360 | 0..360 | continuous | True |
| LUNAR_ILLUMINATION_FRACTION | LUNAR_PHASE_ANGLE_DEG | fraction | (1 - cos(phase angle)) / 2 | 0..1 | continuous | True |
| MOON_PHASE_NAME | LUNAR_AGE_DAYS | category | age-bin label | named phases | categorical | True |
| NIGHT_HOURS | solar altitude | h | count of local-day dark samples * timestep | 0..local civil-day hours (23/24/25) | continuous | True |
| MOON_VISIBLE_HOURS | lunar altitude | h | count of above-horizon samples * timestep | 0..local civil-day hours (23/24/25) | continuous | True |
| MOON_VISIBLE_DARK_HOURS | solar and lunar altitude | h | count of dark and moon-above-horizon samples * timestep | 0..local civil-day hours (23/24/25) | continuous | True |
| MOONLIT_DARK_HOURS | MOON_VISIBLE_DARK_HOURS, lunar illumination | equivalent h | sum(illumination fraction * timestep) when dark and moon visible | 0..local civil-day hours (23/24/25) | continuous | True |
| MOON_VISIBLE_DARK_FRACTION | MOON_VISIBLE_DARK_HOURS, NIGHT_HOURS | fraction | visible-dark hours / night hours; null if no night | 0..1 or null | continuous | True |
| MOONLIT_DARK_FRACTION | MOONLIT_DARK_HOURS, NIGHT_HOURS | fraction | moonlit equivalent hours / night hours; null if no night | 0..1 or null | continuous | True |
| WEIGHT_LUNAR_ILLUMINATION | LUNAR_ILLUMINATION_FRACTION | fraction | deterministic alias | 0..1 | continuous | False |
| WEIGHT_MOONLIT_DARK_HOURS | MOONLIT_DARK_FRACTION | fraction | deterministic alias; null if no night | 0..1 or null | continuous | False |

Missingness: not applicable to complete deterministic output. Nullable exceptions: `MOON_VISIBLE_DARK_FRACTION`: null when NIGHT_HOURS is zero; `MOONLIT_DARK_FRACTION`: null when NIGHT_HOURS is zero; `WEIGHT_MOONLIT_DARK_HOURS`: null when NIGHT_HOURS is zero. Limitation: Approximate geometry; excludes weather and terrain.
