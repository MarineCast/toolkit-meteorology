# Products and interpretation

[Documentation index](README.md)

The checked-in [feature catalog](../config/feature_catalog.yaml) documents field names, units,
roles and declared product paths. The [Arrow schemas](../src/meteorology/core/data/meteorological_schemas.py)
are the executable type contract. Catalog materialization status reflects the workspace in which
it was generated; checked-in metadata does not certify a regional release.

| Product | Row identity | Support / partitioning |
| --- | --- | --- |
| Atmospheric support | `H3_INDEX` | Full bounding box at R4, R5 and R6 |
| HRRR samples | `H3_INDEX × VALID_TIME_UTC` | R5; year/date partitions |
| HRRR inventory | `VALID_TIME_UTC` | One record per expected acquisition time |
| Nearest-grid crosswalk | `H3_INDEX × SOURCE_GRID_HASH` | R5; source-grid-hash partitions |
| Daily weather | `H3_INDEX × DATE` | R5; year/date partitions |
| Daily daylight | `H3_INDEX × DATE` | R4; year partitions |
| Day-of-year daylight | `H3_INDEX × DAY_OF_YEAR` | R4 lookup |
| Daily lunar | `H3_INDEX × DATE` | R5; year partitions |

Dates use the configured product timezone; source timestamps retain UTC identity. H3 resolution
is part of each product's support contract even where it is not included in the row key.
Do not join rows by order or substitute water-only H3 support for atmospheric support.

## Weather

Fields describe temperature, humidity, wind, gusts, visibility, cloud cover, pressure and estimated
precipitation. QC, coverage, sample counts and source metadata remain separate from physical values.
The daily product uses six four-hourly core-weather analyses. Matched precipitation forecasts are
initialized one hour earlier at forecast hour one, with the same valid times.

`PRECIP_MM_DAY_ESTIMATE` sums six rate samples multiplied by four hours. It is an estimate in
millimetres, not a true hourly-integrated 24-hour accumulation. Missing or invalid source support
cannot be interpreted as dry weather. Source timestamps, field identity and units must validate.

## Astronomy

Daylight describes daily solar exposure and a compact day-of-year lookup. Lunar products describe
approximate phase, illumination and moonlit darkness. Their deterministic aliases remain in
scientific products even when the optional model policy excludes them from a model matrix.
Astronomy omits terrain horizons, clouds, artificial light and ephemeris-grade corrections.

## Consuming products

Read and validate manifests before consuming tables, honor product grain and timezone, check key
uniqueness, and preserve unavailable/unknown values separately from zero. A product's environmental
measurements do not imply human presence, reporting probability, detection, or species occurrence.
See [contracts](CONTRACTS.md) and [sources](DATA_SOURCES.md) for detailed limitations.
