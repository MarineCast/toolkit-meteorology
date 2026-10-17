# Python API

## Approved coastal weather

```python
from meteorology import get_weather, ALLOWED_VARIABLES, check_weather

result = get_weather("2009-01-01", "2009-01-02", ALLOWED_VARIABLES,
                     study_config="/path/to/config/study.v1.json",
                     output_dir="scratch/weather", cache_dir="/path/to/source-cache")
check_weather(result.catalog_path, study_config="/path/to/config/study.v1.json")
```

`MARINECAST_STUDY_CONFIG` can supply the study instead of the keyword. The allowed
families are `temperature`, `dewpoint`, `relative_humidity`, `wind`, `cloud_cover`,
`pressure`, and `precipitation`. The return value exposes three file paths; it does
not load the full history into a DataFrame. See [coverage, cache and scientific
contracts](../regional-api.md#approved-coastal-api).

::: meteorology.regional.get_weather
    options:
      show_source: false

::: meteorology.regional.check_weather
    options:
      show_source: false

## Rectangle compatibility API

```python
from meteorology import get_data, get_metric

result = get_data("2009-01-01", "2009-01-31")
result = get_metric("wind", "2009-01-01", "2009-01-31")
```

The [regional API](../regional-api.md) uses ECMWF geographic ERA5 Zarr and returns
paths to wide hourly/daily H3 R5 Parquets and `catalog.json`. Source cache and
publication controls live outside the three-file output directory. Dates are
inclusive UTC days; each call replaces its selected output set.

::: meteorology.regional.get_data
    options:
      show_source: false

::: meteorology.regional.get_metric
    options:
      show_source: false

## Explicit CDS historical alternative

The existing monthly-job ERA5 interface is
[`meteorology.backfill_weather`](../era5-backfill.md); the disk-backed result
provides `metadata`, `iter_tables()`, bounded `to_pandas(max_rows=...)` and a
streamed `to_parquet(path)` export. Its partition companions remain the
authoritative provenance; retain them with the output and raw source cache.

## Selective weather requests

Import the getters from `meteorology`. They accept inclusive UTC dates, a WGS84
`(west, south, east, north)` bbox and an explicit `"hrrr"` or `"era5"` source;
workspace and study configuration are unnecessary. All getters delegate to the
same planner, dependency selection, executor and cache. Read the
[source support matrix and scientific semantics](../request-api.md) before use.

```python
from meteorology import get_weather_request

preview = get_weather_request(
    variables=["temperature", "wind"],
    start_date="2024-01-01", end_date="2024-01-01",
    bbox=(-124.0, 48.0, -123.0, 48.5), data_source="hrrr",
    dry_run=True,
)
```

`dry_run=True` returns a planning dictionary without network access or writes.
Execution returns `WeatherResult`. `get_precip` is exactly the
`get_precipitation` alias. ERA5 snowfall is millimetres of water equivalent falling
during the interval, distinct from snow depth. Unsupported source/variable/support
combinations fail before acquisition.

::: meteorology
    options:
      members:
        - get_weather_request
        - get_temperature
        - get_wind
        - get_cloud_cover
        - get_pressure
        - get_relative_humidity
        - get_dewpoint
        - get_visibility
        - get_precipitation
        - get_snowfall
      show_source: false

### Shared keyword options

The root getters forward their `**options` to this shared implementation. Options
include frequency, support, cache location, limits, dry run, explicit refresh,
`result_id` pinning and `retained_cache_bytes` namespace quota.
Committed operational warnings are exposed in result metadata;
`WeatherResult.reconcile()` repairs pending lifecycle accounting without acquiring data. The v2 lineage and
source-vintage policies are described in [request API](../request-api.md).
The generated-GRIB fixture options always label their inputs synthetic.

::: meteorology.request.get_weather
    options:
      show_source: false

### Result methods

`from meteorology import WeatherResult` exposes the following class lazily.
Use the getters to construct it. `.metadata` returns the selected view's lineage
and coverage; `.to_pandas()` materializes selected rows, while `.to_parquet(path)`
streams an atomic export with embedded JSON metadata. The
[cache and provenance contract](../request-api.md#results-caching-and-provenance)
describes immutable inputs, subset reuse and corruption handling.

::: meteorology.request_cache.WeatherResult
    options:
      members:
        - metadata
        - to_pandas
        - to_parquet
      show_source: false

## Existing family APIs

The following APIs retain the configured family workflows. Existing deeper
imports remain available for advanced usage, but internal module names are not
a stable compatibility promise.

```python
import meteorology

print(meteorology.__version__)
config = meteorology.load_config()  # selected workspace
meteorology.build_spatial_support()
meteorology.acquire_hourly_weather(local_date="2024-01-02", decoded_dir="retained-f00", dry_run=True)
preview = meteorology.download_surface_weather(
    start_date="2024-01-02", end_date="2024-01-02", dry_run=True
)
print(preview["expected_times"])
```

For a Python process outside the workspace, set `METEOROLOGY_WORKSPACE` before loading configuration. Use `validate_product(manifest_path)` to check a built family. `export_daily_matrix([weather_manifest, daylight_manifest, lunar_manifest], output)` preserves native R4/R5 support. `freeze_release(manifest_path, output_root)` archives a validated family under its content-derived release ID.

::: meteorology
    options:
      members:
        - load_config
        - build_spatial_support
        - download_surface_weather
        - build_surface_weather
        - acquire_hourly_weather
        - build_hourly_weather
        - build_daylight
        - build_lunar
        - export_daily_matrix
        - export_weather_summary
        - validate_product
        - freeze_release
      show_source: false

The [method registry](https://github.com/MarineCast/toolkit-meteorology/blob/main/src/meteorology/methods.py), [variable inventory](variables.md), [schemas](https://github.com/MarineCast/toolkit-meteorology/blob/main/src/meteorology/core/data/meteorological_schemas.py) and [product contracts](../CONTRACTS.md) define the scientific interpretation beyond the function signatures.

## Candidate processing contracts

The following deeper modules provide time, interval, atmospheric derivation
and climate-context contracts. The hourly producer uses the temporal
contracts; the remaining modules do not publish new family manifests:

```python
from meteorology.temporal_products import hourly_utc_instants, validate_hourly_records, summarize_hourly_window
from meteorology.precipitation_intervals import sum_exact_precipitation_intervals
from meteorology.atmospheric_summaries import dewpoint_depression_c, gust_factor
from meteorology.climate_context import validate_roni_rows, select_roni_asof

assert len(hourly_utc_instants("2024-03-10", "America/Los_Angeles")) == 23
assert dewpoint_depression_c(5.0, 2.0) == 3.0
assert gust_factor(0.0, 0.0, 0.0) is None
```

`validate_hourly_records` requires complete f00 core fields at distinct real UTC
hours. `summarize_hourly_window` reports sampled extrema and coverage without
bridging missing hours. `sum_exact_precipitation_intervals` requires already
verified source accumulation intervals in millimetres; a PRATE snapshot cannot
be passed off as an amount. Each interval declares `product_kind`:
`forecast_accumulation` permits availability after initialization but before
the future interval end, while `retrospective_accumulation` requires
availability at or after the interval end. Both enforce the as-of cutoff.
This arithmetic helper does not publish a precipitation product.
`select_roni_asof` returns no historical value
without an actual retained publication timestamp. The separate
[hourly weather producer](../hourly-weather.md) invokes the hourly contracts
through its published H3 family. See the
[implementation tracker](../IMPLEMENTATION_TRACKER.md) for unverified provider,
schema and scientific gates.
