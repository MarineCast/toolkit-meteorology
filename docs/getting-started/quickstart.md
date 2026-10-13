# Quickstart

Use Python 3.11–3.13 on macOS or Linux. Selective requests use explicit dates,
bounds and source, without workspace initialization. The separate family example
uses a one-day Pacific Northwest configuration shipped in the wheel.

## Install and inspect

From a clone:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[native-pilot]'
meteorology --help
meteorology sources
meteorology variables --data-source hrrr
```

For ERA5 use `python -m pip install '.[era5]'`. Core-only family processing needs
`python -m pip install .`; HTML inspectors additionally use `.[visualization]`.
A locally built wheel can be installed with `python -m pip install
/path/to/toolkit_meteorology-0.1.0-py3-none-any.whl`. Installed commands run outside
the checkout. These instructions do not establish PyPI publication.

## Preview, then retrieve temperature

```bash
meteorology get temperature --start-date 2024-01-01 --end-date 2024-01-01 \
  --bbox -124 48 -123 48.5 --data-source hrrr --dry-run
```

This read-only preview reports dependencies, support, exact UTC boundaries,
cache status and resource caps. The request is one inclusive UTC day, with HRRR
H3 R5 output by default. To execute the same bounded request:

```bash
meteorology get temperature --start-date 2024-01-01 --end-date 2024-01-01 \
  --bbox -124 48 -123 48.5 --data-source hrrr \
  --cache-dir ./weather-cache --output temperature.parquet
```

This command contacts NOAA and writes retained inputs, result partitions and an
export. An identical complete cache hit makes zero provider requests. New live
query compatibility was not executed during this refactor.

The equivalent Python request is:

```python
from meteorology import get_temperature

result = get_temperature(
    start_date="2024-01-01", end_date="2024-01-01",
    bbox=(-124.0, 48.0, -123.0, 48.5), data_source="hrrr",
    cache_dir="./weather-cache",
)
print(result.metadata)
df = result.to_pandas()
result.to_parquet("temperature.parquet")
```

`WeatherResult` stays on disk until materialization is requested. Daily long rows
include date, support identity, metric, value, units, status and coverage. A
temperature mean requires 24 finite UTC samples; incomplete days remain null.
Use `get_weather(variables=["temperature", "wind"], ...)` to acquire shared inputs
once, or `frequency="hourly"` to keep hourly values. Consult the
[source support matrix, credentials and error guide](../request-api.md) before
changing source, quantity or support.

## Offline request demo

From the clone, generate small ERA5 fields and run the actual public getter:

```bash
python -m pip install '.[era5]'
python demo/request_offline.py --output ./request-demo
```

The demo exports synthetic temperature and snowfall water equivalent, then
verifies repeat cache reuse. It needs no credentials or provider access; it is
software evidence, not a live-source or regional-accuracy qualification.

## Offline family products

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

## Existing HRRR family: one local day

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
