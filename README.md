# Meteorology Toolkit

<img src="docs/assets/banner.png" alt="Engraved weather station amid clouds, rain, mountains, and pressure systems" width="100%">

[![Offline package checks](https://github.com/MarineCast/toolkit-meteorology/actions/workflows/tests.yml/badge.svg)](https://github.com/MarineCast/toolkit-meteorology/actions/workflows/tests.yml) · [Documentation](https://marinecast.github.io/toolkit-meteorology/) · [Scientific methodology](docs/methodology.md) · [Variable inventory](docs/reference/variables.md)

**Reproducible, provenance-aware spatial and temporal meteorological and astronomical products for environmental and ecological modeling.** `toolkit-meteorology` is an independently installable Python distribution. Import `meteorology` or use the `meteorology` command. It does not require an OrcaCast checkout.

| Product | Meaning | Native support |
| --- | --- | --- |
| Atmospheric H3 support | Cell centroids inside a configured WGS84 box, including land and water | R4, R5, R6 |
| Surface weather | Six four-hourly HRRR `sfc/f00` analysis samples; matched `sfc/f01` precipitation-rate snapshots | R5 × local date |
| Hourly atmosphere | Separate actual UTC `sfc/f00` core analyses from retained decoded grids | R5 × UTC hour; 23/24/25 hours per local date |
| Daylight | Approximate geometric day length, solar profile and daylight weights | R4 × local date; day-of-year lookup |
| Lunar context | Approximate phase, disk illumination and geometrical moon visibility | R5 × local date |
| Daily matrix | Optional union of native daily families, with unsupported component values null | R4/R5 × local date |

**October 2026 implementation status:** The six-snapshot daily weather/astronomy contract remains unchanged. A [separate hourly family](docs/hourly-weather.md) now publishes complete actual-hour f00 H3 rows from retained decoded inputs. Its offline synthetic workflow is tested; representative real decoded GRIB compatibility and regional scientific accuracy remain unrun. Interval-amount, atmospheric-summary, and RONI modules remain candidate contracts. The [milestone tracker](docs/IMPLEMENTATION_TRACKER.md) separates implementation, source compatibility, and empirical acceptance.

The package is primarily **retrospective**. `PRECIP_MM_DAY_ESTIMATE` sums six forecast-rate snapshots multiplied by nominal four-hour intervals; it is not a measured 24-hour precipitation accumulation. Weather and astronomy do not measure species occurrence, observer effort, reporting or detection probability. Read [limitations](docs/limitations.md) before using the values as model predictors.

## Start in under five minutes, offline

Use Python 3.11–3.13 on macOS or Linux. From a clone:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[test]'
meteorology --workspace ./weather-demo init
meteorology --workspace ./weather-demo example-offline
meteorology --workspace ./weather-demo validate \
  --daily-matrix ./weather-demo/outputs/synthetic-daily-matrix.parquet
```

`example-offline` creates explicitly synthetic one-day HRRR-like inputs, then runs the normal support, weather, daylight, lunar and matrix builders. It requires a fresh workspace, makes no network requests and refuses to replace existing products. It is a package workflow check, not a live NOAA data certification. The [quickstart](docs/getting-started/quickstart.md) also shows a clean wheel install outside the checkout.

## Real HRRR workflow

Live acquisition uses optional Herbie, cfgrib and ecCodes support:

```bash
python -m pip install '.[acquisition]'
meteorology --workspace ./weather-real init
meteorology --workspace ./weather-real build spatial-support
meteorology --workspace ./weather-real download surface-weather \
  --start-date 2024-01-02 --end-date 2024-01-02 --dry-run
meteorology --workspace ./weather-real download surface-weather \
  --start-date 2024-01-02 --end-date 2024-01-02 --workers 4
meteorology --workspace ./weather-real build surface-weather
meteorology --workspace ./weather-real validate \
  --manifest ./weather-real/data/processed/domain/environmental_layer/meteorological/surface_weather/MANIFEST.json
```

Inspect the initialized bounding box, timezone, date and output paths first. A request spanning more than seven local days requires explicit `--allow-large-download`; `--dry-run` reports the intended time span and cycle count without contacting the provider. Acquisition resumes verified samples. The package never writes runtime products into `site-packages`.

## Python API

```python
import meteorology

print(meteorology.__version__)
config = meteorology.load_config()  # METEOROLOGY_WORKSPACE or current directory
meteorology.build_spatial_support()
preview = meteorology.download_surface_weather(
    start_date="2024-01-02", end_date="2024-01-02", dry_run=True
)
print(preview["expected_times"])
```

The root API also provides `build_surface_weather`, `build_daylight`, `build_lunar`, `export_daily_matrix`, `validate_product` and `freeze_release`. See the [API reference](docs/reference/api.md).

## Scientific and release contracts

- [Methodology](docs/methodology.md) explains source identity, local dates and DST, nearest-grid sampling, daily reductions, vector wind, precipitation and astronomical approximations.
- [Product contracts](docs/CONTRACTS.md) and [Arrow-backed variable inventory](docs/reference/variables.md) define keys, units, missingness, schemas and scientific method versions.
- Every family manifest records software version, method version, run ID, content-derived release ID, configuration, input/output checksums, source attribution and limitations. `meteorology validate` checks a release; `meteorology freeze-release` copies it to a checksum-backed release-ID directory and refuses to replace an existing one.
- The package's [modeling guidance](docs/guides/downstream-modeling.md) calls out temporal/spatial leakage and availability at prediction time. Its optional feature policy is a downstream selection aid, not a universal ecological recommendation.

## Development and release status

```bash
python -m pip install '.[dev]'
python -m pytest -q
ruff check src tests
mkdocs build --strict
python -m build
python -m twine check dist/*
```

See [production readiness](PRODUCTION_READINESS.md), the [release checklist](docs/development/release-process.md), and [CHANGELOG.md](CHANGELOG.md) for verified status and remaining owner actions. Creating a tag, publishing to PyPI and running a regional NOAA rebuild are separate reviewed actions.

Apache-2.0 software license. NOAA/source data rights and attribution are described in [sources and rights](docs/DATA_SOURCES.md).
