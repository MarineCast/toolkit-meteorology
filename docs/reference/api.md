# Python API

The root package exposes the supported high-level workflow. Existing deeper imports remain available for advanced usage, but internal module names are not a stable compatibility promise.

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
be passed off as an amount. `select_roni_asof` returns no historical value
without an actual retained publication timestamp. The separate
[hourly weather producer](../hourly-weather.md) invokes the hourly contracts
through its published H3 family. See the
[implementation tracker](../IMPLEMENTATION_TRACKER.md) for unverified provider,
schema and scientific gates.
