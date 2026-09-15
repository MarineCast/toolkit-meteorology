# Installation and workspace setup

[Documentation index](README.md)

## Install

Use Python 3.11 or newer on Linux or macOS. Publication uses POSIX file locks. From the toolkit
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

For live HRRR acquisition, install `.[acquisition]` instead, or combine extras as
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

The example defaults cover the Northeast Pacific and several years. Edit geography and dates
before running a build. Start with a short interval in a separate workspace.

## Select the workspace consistently

The CLI's `--workspace` overrides `METEOROLOGY_WORKSPACE` for that command. Otherwise the environment
variable selects the root; without either setting, the current directory is the root.

```sh
export METEOROLOGY_WORKSPACE=/path/to/weather
meteorology stages
python -m meteorology --help
```

Python APIs use the same environment-variable/current-directory rule. Package installation paths
are not writable data workspaces. Relative configured output paths resolve under the workspace;
absolute paths remain absolute. Continue with [configuration](CONFIGURATION.md).
