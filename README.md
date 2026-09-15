# Meteorology Toolkit

Species-neutral HRRR weather, daylight, lunar calculations and full-bbox atmospheric H3 support.
The distribution is **`toolkit-meteorology`**; the Python package and command are **`meteorology`**.
It installs independently of OrcaCast and sibling toolkits.

## Documentation

Start with the [documentation index](docs/README.md) for setup, configuration, workflows,
product contracts, architecture and development guidance.

## Installation

Python 3.11+ on Linux or macOS. Transactional publication uses POSIX file locks.

```sh
python -m pip install -e '.[test]'
python -m pytest -q
meteorology --help
```

For a regular install use `python -m pip install .`. Install `.[acquisition]` to acquire HRRR
GRIB data through Herbie, cfgrib and ecCodes. Offline builds and astronomy do not require Herbie.
A compatible ecCodes runtime is required for live GRIB decoding.

## Workspace and execution

Paths resolve under the current directory, `METEOROLOGY_WORKSPACE`, or the CLI's `--workspace`.
Package installation never makes site-packages a data/output directory. Initialize an external
workspace, then edit `config/common.yaml` and `config/data/environment_meteorological.yaml`:

```sh
meteorology --workspace /path/to/weather init
meteorology --workspace /path/to/weather build spatial-support
meteorology --workspace /path/to/weather download surface-weather --start-date 2024-01-02 --end-date 2024-01-02 --workers 4
meteorology --workspace /path/to/weather build surface-weather
meteorology --workspace /path/to/weather build daylight --start-date 2024-01-02 --end-date 2024-01-02
meteorology --workspace /path/to/weather build lunar --start-date 2024-01-02 --end-date 2024-01-02
meteorology --workspace /path/to/weather inspect surface-weather --date 2024-01-02
meteorology --workspace /path/to/weather catalog
meteorology --workspace /path/to/weather feature-policy
```

Set the configured weather date range to match the acquired range before building. The inherited
regional defaults cover the Northeast Pacific and multiple years; inspect them before acquisition
or astronomy builds. `init` preserves existing files. Each family supports `--help`; `stages`
lists build order. Acquisition is explicit; builds read local inputs. Repeated builds use the
inherited family staging, manifest validation and transactional replacement semantics. There is
no whole-domain candidate release or promotion command in this extraction. Use a separate workspace
for experiments. Never point it at validated OrcaCast data for an exploratory build.

Python APIs use the same workspace contract:

```python
from meteorology.config import load_meteorological_config
from meteorology.spatial_support.build import build_meteorological_spatial_support

config = load_meteorological_config()  # after workspace initialization
build_meteorological_spatial_support(config.path)
```

## Scientific scope

- H3 support includes land and water, at R4/R5/R6; it is not a wet-cell universe.
- Weather is R5, one row per H3 cell and local date, using six four-hourly HRRR f00 analyses.
- Precipitation uses matched f01 forecasts and is a six-snapshot daily estimate, not a measured
  24-hour accumulation. Source issue time, lead time and valid time remain distinct.
- Daylight is R4 daily plus a day-of-year lookup; lunar phase/moonlight is R5 daily. Approximate
  astronomy does not model clouds, terrain horizons, refraction or artificial light.
- Missing, unsupported and non-finite inputs stay distinct from observed zero and block weather
  publication when the required support is incomplete. Units, Arrow schemas, keys, checksums,
  provenance, and acquisition-resumption behavior are retained.
- This implementation is retrospective weather plus deterministic astronomy. General future-weather
  forecasting remains outside its implemented scope. Weather is not sighting probability.

See [contracts and workflow](docs/CONTRACTS.md), [source rights and limitations](src/meteorology/DATA_SOURCES.md),
[development](docs/DEVELOPMENT.md), and [migration and validation](docs/MIGRATION.md).
The inherited `modeling.feature_policy` is an optional downstream ecological-model selection policy;
it never changes scientific products and is not a universal recommendation for every application.

## Validation boundary

Offline fixtures exercise strict acquisition identity, aggregation, missingness, failure recovery,
manifest checksums, astronomy and HTML generation. Live acquisition, regional rebuilds, visual map
QA, remote CI and OrcaCast model integration are separate checks. No source datasets are bundled.
