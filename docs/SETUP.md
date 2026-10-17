# Installation and workspace setup

[Documentation index](index.md)

## Install

Use Python 3.11–3.13 on Linux or macOS. Publication uses POSIX file locks. From the toolkit
checkout, install into your chosen Python environment:

```sh
python -m pip install .
meteorology --help
```

For development and offline tests:

```sh
python -m pip install -e '.[test]'
python -m pytest -q
```

For the primary coastal API, core installation is sufficient. Set
`MARINECAST_STUDY_CONFIG` to the approved owner study configuration and retain its
registry/mask inputs. Configure CDS credentials only for uncached ECMWF access;
see the [quickstart](getting-started/quickstart.md). No `meteorology init` is required.

For selective HRRR requests install `.[native-pilot]`; the legacy CDS/GRIB ERA5
route uses `.[era5]`.
HTML map inspectors use `.[visualization]`. Generic getters need no workspace
initialization; see [weather requests](request-api.md). For legacy live Herbie
acquisition, install `.[acquisition]` instead, or combine extras as
`'.[test,acquisition]'`. Acquisition uses Herbie, cfgrib and a compatible ecCodes runtime.
The package's offline build and astronomy paths do not require Herbie. See
[development](DEVELOPMENT.md) for regular wheel validation.

## Initialize a data workspace

Choose a directory for configuration, downloaded inputs and generated products:

```sh
meteorology --workspace /path/to/weather init
meteorology --workspace /path/to/weather stages
```

Replace `/path/to/weather` with your own location. Initialization copies five configuration and
reference-metadata files, preserves existing files, and performs no acquisition or build.

| File | Purpose |
| --- | --- |
| `config/common.yaml` | Named geographic areas |
| `config/data/environment_meteorological.yaml` | Source, dates, support and artifact paths |
| `config/data/presentation_settings.yaml` | Map colors, basemap and export root |
| `config/feature_catalog.yaml` | Product and field metadata |
| `config/model_feature_policy.yaml` | Optional inherited ecological-model selection policy |

The packaged example covers a Pacific Northwest bounding box and one local day. Edit geography,
timezone and dates before a real build. The date range is intentionally small to avoid a surprise
multi-year acquisition.

## Select the workspace consistently

The CLI's `--workspace` overrides `METEOROLOGY_WORKSPACE` for that command. Otherwise the environment
variable selects the root; without either setting, the current directory is the root.

```sh
export METEOROLOGY_WORKSPACE=/path/to/weather
meteorology stages
python -m meteorology --help
```

Legacy family Python APIs use the same environment-variable/current-directory rule.
The new `get_*` functions use explicit bounds/source and `cache_dir` independently. Package installation paths
are not writable data workspaces. Relative configured output paths resolve under the workspace;
absolute paths remain absolute. Continue with [configuration](CONFIGURATION.md).
