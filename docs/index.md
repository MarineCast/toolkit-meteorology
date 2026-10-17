![Engraved weather station amid clouds, rain, mountains, and pressure systems](assets/banner.png)

# Coastal ERA5 weather

The supported product is hourly and daily weather for the approved coastal H3 R5
registry. One API uses ECMWF's fast geographically chunked ERA5 archive and writes
two Parquets plus `catalog.json` directly for the approved cells.

```python
from meteorology import get_weather, ALLOWED_VARIABLES, check_weather

# Configure MARINECAST_STUDY_CONFIG with the approved study.v1.json first.
result = get_weather("2009-01-01", "2009-01-31", ALLOWED_VARIABLES)
check_weather(result.catalog_path, study_config="/path/to/config/study.v1.json")
```

The seven allowed families are temperature, dewpoint, relative humidity, wind,
cloud cover, pressure and precipitation. All seven produce 14 daily and 10 hourly
metric columns. The current approved product has 463 cells. Dates are inclusive
UTC days; precipitation requires the following midnight accumulation. Unsupported
values remain null. This is retrospective reanalysis, with historical operational
availability unknown; no forecast qualification is implied.

Start with the [quickstart](getting-started/quickstart.md), then the
[coastal API and checks](regional-api.md#approved-coastal-api).
The [current product](coastal-product.md) documents the original source vintages,
coverage and partial final day. [Python](reference/api.md) and
[command](reference/cli.md) references describe the installed interfaces.

The primary path uses three existing modules: source/cache, scientific reduction,
and API/publication/checks, plus shared registry and transaction utilities. It
processes one day at a time and reuses checksummed cached source chunks. Each call
replaces its requested output range; use a scratch output directory for experiments.

## Compatibility workflows

The [rectangle exporter](regional-api.md#rectangle-compatibility-api),
[explicit-source requests](request-api.md), [CDS backfill](era5-backfill.md),
[HRRR families](hourly-weather.md), daylight and lunar workflows remain available
for existing callers. They are not required to produce the approved coastal files.
The former five-argument `get_weather` is named `get_weather_request`.
See [architecture](ARCHITECTURE.md) for the retained modules and
[contracts](CONTRACTS.md) for their separate scientific semantics.

Python 3.11–3.13 on macOS/Linux is supported by package configuration. See the
[verification record](coastal-product.md#primary-api-verification-2026-10-17) for
the environment and checks actually exercised. Weather context does not measure
species occurrence, reporting or detection probability.
