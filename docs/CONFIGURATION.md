# Configuration

[Documentation index](index.md)

The canonical producer document is
[`config/data/environment_meteorological.yaml`](https://github.com/MarineCast/toolkit-meteorology/blob/main/config/data/environment_meteorological.yaml).
The loader requires `schema_version: 3` and rejects unknown keys in validated sections.
Use the initialized workspace copy for runs; repository templates also have packaged copies.

## Geography

The example resolves `example_area` in [`config/common.yaml`](https://github.com/MarineCast/toolkit-meteorology/blob/main/config/common.yaml):

```yaml
spatial_support:
  bbox:
    area: example_area
  resolutions: [4, 5, 6]
```

Change the named area's `bbox_wgs84` values to select your region. Explicit bounds are also accepted
with the following nesting; this is a fragment of the full configuration:

```yaml
spatial_support:
  bbox:
    bbox:
      min_lon: -124.0
      max_lon: -123.0
      min_lat: 48.0
      max_lat: 49.0
  resolutions: [4, 5, 6]
```

Bounds are longitude/latitude in degrees. Atmospheric support includes land and water. A very small
box can contain no H3 centers at the coarsest resolution; the support build rejects empty support.
The shipped `example_area` ends at 49.70°N because its former northwest corner at
50.00°N, 125.80°W lies outside the decoded NOAA CONUS HRRR grid. The acquisition now
checks the native grid footprint and the selected crop before it samples H3 cells.
Changing the example bounds requires rebuilding spatial support and reacquiring weather.

## Fixed product contracts

| Setting | Required value |
| --- | --- |
| Support resolutions | `[4, 5, 6]` |
| Weather H3 resolution | `5` |
| Weather sample interval | `4` hours |
| HRRR model / product | `hrrr` / `sfc` |
| Core weather forecast hour | `0` |
| Precipitation forecast hour | `1` |
| Daylight H3 resolution | `4` |
| Lunar H3 resolution | `5` |

These values are validated scientific contracts, not freely interchangeable tuning parameters.
Changing them requires producer/schema changes and new validation.

## Dates and astronomy

The shipped example uses a single explicit local date. For a bounded weather run, set both
`surface_weather.time.start_date` and `end_date` to explicit ISO dates. `latest_complete` is also
accepted as the weather end date; the loader derives a cutoff
using the configured timezone and availability lag. A failed acquisition keeps its frozen range
for resumption. CLI date overrides are available for download and dated builds.
Requests over seven local days require explicit `--allow-large-download`; preview with `--dry-run`.

Daylight and lunar sections have their own date ranges, timezone and integration timestep. Daylight
also defines the low-sun threshold and default weight; lunar settings define its UTC phase sample
hour, darkness threshold and minimum moon altitude. Keep these settings with product provenance.

## Outputs and presentation

Each family declares output directories and a manifest path. Weather additionally declares raw
sample storage, working inventory, canonical inventory and acquisition manifest locations.
Preserve their relationships when changing paths; use a separate workspace for an independent run.

[`presentation_settings.yaml`](https://github.com/MarineCast/toolkit-meteorology/blob/main/config/data/presentation_settings.yaml) controls export root,
color maps, basemap, zoom and static color. Inspectors accept `--presentation-config` and
`--output-path`. Relative command-line export destinations may follow the individual exporter;
use an absolute `--output-path` when running outside the workspace.

## Validate without building

After initialization and configuration edits:

```python
from meteorology.config import load_meteorological_config

config = load_meteorological_config()
print(config.bbox)
print(config.surface_weather.start_date, config.surface_weather.resolved_end_date())
print(config.surface_weather.daily_output_dir)
```

This validates configuration, not source coverage or existing products.
