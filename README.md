# Meteorology Toolkit

<img src="docs/assets/banner.png" alt="Engraved weather station amid clouds, rain, mountains, and pressure systems" width="100%">

[![Offline package checks](https://github.com/MarineCast/toolkit-meteorology/actions/workflows/tests.yml/badge.svg)](https://github.com/MarineCast/toolkit-meteorology/actions/workflows/tests.yml) · [Documentation](https://marinecast.github.io/toolkit-meteorology/) · [Scientific methodology](docs/methodology.md) · [Variable inventory](docs/reference/variables.md)

Retrieve source-selective weather through `meteorology` Python functions or the
matching CLI. This independent toolkit preserves source grids, units, coverage,
missingness and checksum-backed lineage. Python 3.11–3.13; macOS or Linux.

## Install from a clone

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[native-pilot]'  # selective HRRR
# For ERA5 instead: python -m pip install '.[era5]'
```

Core installation is `python -m pip install .`. HTML inspectors use the optional
`.[visualization]` extra. Legacy Herbie acquisition uses `.[acquisition]`.
These are local-source installation commands; no PyPI publication is claimed.

## Retrieve with Python

```python
from meteorology import get_temperature

result = get_temperature(
    start_date="2024-01-01", end_date="2024-01-01",
    bbox=(-124.0, 48.0, -123.0, 48.5), data_source="hrrr",
    cache_dir="./weather-cache",
)
df = result.to_pandas()
result.to_parquet("temperature.parquet")
print(result.metadata)
```

The four required arguments also work positionally. For several variables, use
`get_weather(variables=["temperature", "wind", "cloud_cover"], ...)`: their input
dependencies are acquired once. This bounded real-data example performs provider
access; the new live path has not been executed during this refactor.

## Retrieve with the CLI

```sh
meteorology get temperature --start-date 2024-01-01 --end-date 2024-01-01 \
  --bbox -124.0 48.0 -123.0 48.5 --data-source hrrr \
  --cache-dir ./weather-cache --output temperature.parquet
```

Append `--dry-run` to preview exact UTC boundaries, dependencies, support, cache
hits, missing work and resource limits without network access or writes.
`meteorology sources` and `meteorology variables --data-source era5` show support.
Help and standalone queries require no initialized workspace or shared study.

## Returned data

Daily output is long `DATE × H3_INDEX × METRIC` data for HRRR R5, or
`DATE × source grid × native point × METRIC` for native output. Rows carry `VALUE`,
`UNITS`, `SOURCE`, `STATUS`, valid/expected hours and coverage. Temperature is 2 m
air temperature in Celsius. Dates are inclusive UTC days; partial daily values
remain null. Hourly output is available with `frequency="hourly"`.

Results remain on disk until `.to_pandas()` is requested. `.to_parquet()` streams
an export with durable JSON metadata: inputs/checksums, source grids and resolution,
spatial method, time semantics, reductions and method/schema/producer versions.
Compatible final-cache hits make zero provider requests; subsets read selected
date/metric partitions. No legacy releases are rewritten.

## Availability and limits

| Variable | HRRR: R5 or native | ERA5: native |
| --- | --- | --- |
| Temperature, wind, cloud cover, sea-level pressure, RH | Supported | Supported; RH derived from temperature/dewpoint |
| Dewpoint | Unsupported | Supported |
| Visibility | Supported | Unsupported |
| Interval precipitation | Unsupported | Supported |
| Interval snowfall water equivalent | Unsupported | Supported |

ERA5 H3 sampling is unqualified and rejected before acquisition. Its 0.25 degree
CDS distribution does not add detail to the roughly 31 km model information.
HRRR exposes the v4 CONUS decoder era from 2020-12-03 and checks actual decoded
footprint and sampling distances. Requests are limited to seven UTC days and a
bounded crop, one worker and explicit resource caps. Transfer sizes are unknown
until measured. ERA5 needs local credentials and manual provider-terms acceptance.

Daily instantaneous quantities use sampled hourly means. Wind speed means the
mean hourly magnitude. ERA5 precipitation and snowfall sum validated one-hour
amounts, including next-midnight data. `get_precip` aliases `get_precipitation`.
Snowfall is **mm water equivalent falling during the interval**, separate from
ground snow depth and snowpack. No fixed fresh-snow depth conversion is used.
The existing six-rate `PRECIP_MM_DAY_ESTIMATE` retains its separate legacy meaning.

Generated-GRIB and mocked-HTTP tests establish software behavior. They do not
establish actual CDS/NOAA compatibility for new queries or regional accuracy.
See [request semantics, source setup and errors](docs/request-api.md),
[performance and verification evidence](docs/request-refactor-evidence.md), and
[scientific limitations](docs/limitations.md).

## Start offline

A small source-fixture request requires no credentials or provider access:

```sh
python -m pip install '.[era5]'
python demo/request_offline.py --output ./request-demo
```

For a core-only offline run through the existing weather and astronomy families:

```sh
meteorology --workspace ./weather-demo init
meteorology --workspace ./weather-demo example-offline
```

Both label their generated inputs synthetic. The request demo uses the actual
ERA5 normalizer and shared getter; the family demo preserves the existing local-day
products. See [advanced acquisition/build workflows](docs/WORKFLOWS.md),
[legacy Python API](docs/reference/api.md), [shared-study publication](docs/shared-study.md),
and [migration history](docs/MIGRATION.md).

## Development

```sh
python -m pip install -e '.[dev,native-pilot]'
python -m pytest -q
ruff check src tests benchmarks
mkdocs build --strict
python -m build
python -m twine check dist/*
```

See [cleanup inventory](docs/request-cleanup.md),
[production readiness](PRODUCTION_READINESS.md) and [release checklist](docs/development/release-process.md).
No merge, release publication, protected-branch change or data deletion accompanies
this feature branch. Apache-2.0 software; [source rights](docs/DATA_SOURCES.md) apply
separately to provider data.
