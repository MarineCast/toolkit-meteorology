# Quickstart

Python 3.11–3.13 is required. macOS and Linux are the intended platforms because family publication uses POSIX file locks. The example uses a one-day, Pacific Northwest configuration that ships in the wheel. Replace its coordinates and dates before a real build.

## Install and inspect

From a clone:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[test]'
meteorology --help
meteorology variables --json
```

For an installed release, use `python -m pip install toolkit-meteorology` once the owner has published it to PyPI. A checkout is not needed to run the installed command.

## Offline, reproducible product

Run from any directory with a fresh output workspace:

```bash
meteorology --workspace ./weather-demo init
meteorology --workspace ./weather-demo example-offline
meteorology --workspace ./weather-demo validate \
  --daily-matrix ./weather-demo/outputs/synthetic-daily-matrix.parquet \
  --json-output ./weather-demo/outputs/validation.json
```

The example creates deterministic, labeled synthetic HRRR-like fields and runs the same support, weather, daylight, lunar and matrix builders used for real inputs. It exercises publication and validation without network access. It does **not** prove Herbie, ecCodes or the live NOAA archive is working. A second run in the same workspace is refused so existing products are not replaced.

The matrix contains one row per `(DATE, H3_INDEX, H3_RESOLUTION)`. Daylight R4 and weather/lunar R5 values are kept at native resolution; incompatible component fields are null, not zero. The command prints checksums and release IDs for the generated families.

## Real HRRR: one local day

Install the acquisition extra and verify ecCodes is available on your platform:

```bash
python -m pip install '.[acquisition]'
meteorology --workspace ./weather-real init
meteorology --workspace ./weather-real build spatial-support
meteorology --workspace ./weather-real download surface-weather \
  --start-date 2024-01-02 --end-date 2024-01-02 --dry-run
```

Inspect `weather-real/config/common.yaml` for the WGS84 bounding box and `weather-real/config/data/environment_meteorological.yaml` for timezone, date, H3 resolution and paths. Then, only when the stated geographic/time request is intended, run:

```bash
meteorology --workspace ./weather-real download surface-weather \
  --start-date 2024-01-02 --end-date 2024-01-02 --workers 4
meteorology --workspace ./weather-real build surface-weather
meteorology --workspace ./weather-real validate \
  --manifest ./weather-real/data/processed/domain/environmental_layer/meteorological/surface_weather/MANIFEST.json
```

The default sample area still involves many HRRR grid values. Requests over seven local days are refused until `--allow-large-download` is supplied. The `--dry-run` preview reports cycle count, time range, H3 resolution, worker count and destination; no network access occurs. Acquisition resumes checksum-validated samples and limits parallelism to 16 workers.

See [workflows](../WORKFLOWS.md), [configuration](../CONFIGURATION.md) and [methodology](../methodology.md) before expanding the domain or date range.
