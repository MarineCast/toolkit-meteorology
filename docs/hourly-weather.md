# Hourly H3 surface atmosphere

The separate `meteorological.hourly_weather` family publishes one actual HRRR
`sfc/f00` analysis per UTC hour and R5 H3 support cell. It does not interpolate
the six daily samples. A Pacific local day has 23, 24 or 25 source hours on DST
transitions. The row key is `(H3_INDEX, VALID_TIME_UTC)`; `DATE` is the
configured local civil date. The product's Arrow schema is
`hourly-weather-r5-v1`, its method is `hrrr_f00_hourly_h3_r5_v1`, and the
acquisition method is `hrrr_f00_hourly_source_r5_v1`. The old daily method,
schema, matrix export and `PRECIP_MM_DAY_ESTIMATE` are unchanged.

## Sources and physical meaning

The required core is 2 m temperature and relative humidity, earth-relative
10 m U/V and derived speed, surface gust, horizontal visibility, total-cloud
fraction, and mean sea-level pressure. The source selectors and units are the
existing `surface_weather.source.HRRR_VARIABLES` / `normalize_flat_grid`
contract. Although the retained normalized input schema also has f00 PRATE,
the hourly product omits it; the legacy f01 rate and a verified accumulation
amount belong to separate products. Zero wind and zero cloud are valid.
Every required field, hour and H3 cell must be present and finite. The first
schema has no optional physical fields; the explicit optional capability list
is empty. Missing future cloud-layer or radiation support cannot erase core
hours and cannot be zero-filled. H3 values are nearest native-grid point
samples, not H3 area means. Wind U/V have true east/north axes; the retained
source basis is recorded separately.

The source is NOAA/NCEP HRRR surface analysis. Consult NOAA's source terms for
reuse and redistribution of actual source data. Offline tests use labeled
synthetic fixtures. The normalized grid, a separate JSON evidence sidecar,
source object/retrieval URI, retrieval time, native footprint and spacing
allowance are retained and checksummed. A retained bundle alone is not proof
that a real provider supplied those bytes; real-source compatibility requires
representative decoded GRIB evidence and an authorized acquisition run.

`AVAILABLE_AT_UTC` is the configured **assumed fixed lag** after valid time,
not observed provider publication. `SOURCE_RETRIEVED_AT_UTC` records actual
retrieval separately. Forecast leads other than f00, mixed source vintages,
unsupported source fields or geography, missing hours/cells, and invalid
availability block the complete local-day release.

## Offline producer and consumer

Initialize a fresh workspace and build its atmospheric support first. Supply
one retained normalized-grid Parquet plus JSON sidecar for every expected UTC
hour. Filenames use `YYYYmmddTHHZ.parquet` and `.json`. Parquet must match
`surface_weather.source.RAW_SCHEMA`, the same normalized grid schema emitted
by the current HRRR decoder. JSON contains `valid_time_utc`,
`source_grid_hash`, `source_uri`, `source_object_uri`, `retrieved_at_utc`,
`source_evidence_kind`, `native_footprint_wkb_hex`, and
`max_nearest_distance_m`. `source_evidence_kind` is either
`retained_decoded_hrrr` or `synthetic_fixture`; synthetic URIs use
`synthetic://`. Python callers with a genuinely decoded grid can use
`retain_decoded_hourly_grid()` to create the bundle from the existing
`fetch_cropped_hrrr_grid()` result. No CLI operation fetches provider data for
this family.

```bash
meteorology --workspace ./hourly-demo init
meteorology --workspace ./hourly-demo build spatial-support
meteorology --workspace ./hourly-demo download hourly-weather \
  --date 2024-01-02 --decoded-dir ./retained-f00 --dry-run
meteorology --workspace ./hourly-demo download hourly-weather \
  --date 2024-01-02 --decoded-dir ./retained-f00
meteorology --workspace ./hourly-demo build hourly-weather
meteorology --workspace ./hourly-demo inspect hourly-weather
meteorology --workspace ./hourly-demo validate \
  --manifest ./hourly-demo/data/processed/domain/environmental_layer/meteorological/hourly_weather/MANIFEST.json
```

The dry run reports 23/24/25 expected cycles, retained input bytes, and
zero provider requests/network bytes. It does not estimate future live
transfer cost. Acquire copies decoded inputs and sidecars to immutable
content-addressed objects, samples H3, and publishes the inventory/manifest
only after a complete day. Failed acquisition leaves reusable unreferenced
objects for manual inspection. A rerun revalidates the bundles and reuses
matching immutable objects. The build pins the consumed acquisition and
support generations; freeze copies every referenced input for relocation.
Do not delete objects automatically or change the existing daily workspace.

Python consumers can call `meteorology.acquire_hourly_weather`,
`meteorology.build_hourly_weather` and `meteorology.validate_product`. Read the
published Parquet as `H3_INDEX × VALID_TIME_UTC`, with local `DATE` as a
partition label. `summarize_hourly_window` requires an H3 cell selection and
returns sampled extrema, mean and observed-hour coverage over a whole-hour
UTC window. It does not interpolate gaps or turn an instantaneous value into
a measured hourly average.

`meteorology freeze-release --manifest <hourly-manifest> --output-root <root>`
produces a checksum-backed relocatable copy. The standalone hourly family is
not included in `export-daily-matrix`, whose native daily contract is stable.

## Qualification state

The offline synthetic acquisition, build, deep validation, freeze and
relocation path is tested across 23/24/25-hour days. Actual HRRR decoded
fixtures across source eras, source publication timing, requested full-region
coverage, observational accuracy and regional acceptance remain unrun. See
the [implementation tracker](IMPLEMENTATION_TRACKER.md).
