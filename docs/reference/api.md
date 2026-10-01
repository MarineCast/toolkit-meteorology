# Python API

The root package exposes the supported high-level workflow. Existing deeper imports remain available for advanced usage, but internal module names are not a stable compatibility promise.

```python
import meteorology

print(meteorology.__version__)
config = meteorology.load_config()  # selected workspace
meteorology.build_spatial_support()
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
        - build_daylight
        - build_lunar
        - export_daily_matrix
        - validate_product
        - freeze_release
      show_source: false

The [method registry](https://github.com/MarineCast/toolkit-meteorology/blob/main/src/meteorology/methods.py), [variable inventory](variables.md), [schemas](https://github.com/MarineCast/toolkit-meteorology/blob/main/src/meteorology/core/data/meteorological_schemas.py) and [product contracts](../CONTRACTS.md) define the scientific interpretation beyond the function signatures.
