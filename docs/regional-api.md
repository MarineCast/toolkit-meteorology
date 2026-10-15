# Regional ERA5 API

The default historical source for `get_data` and `get_metric` is ECMWF's
geographically chunked ERA5 single-level Zarr store (CC BY 4.0 attribution:
Copernicus Climate Change Service / ECMWF). Existing explicit-source and HRRR
APIs retain their contracts. This is reanalysis, not historical forecast availability.

The default domain is the coastal study rectangle [-129.7, 45.9, -121.5, 51.5]
(west, south, east, north), configurable with `bbox`. H3 resolution 5 uses
full-rectangle atmospheric centroid support, including land and water. Nearest
quarter-degree ERA5 sampling does not create independent H3-scale information.
Dates are inclusive UTC dates. Daily metrics require complete hourly support;
precipitation sums the 24 one-hour accumulations ending 01:00 through next midnight.
Hourly rows use UTC hour-start timestamps, with precipitation for that following
hour and other fields instantaneous at its start. Unknown revision/init/lead metadata remain unknown.
RH uses the existing supported Bolton-water domain; unsupported values stay null.

The public output is two wide Parquets (hourly/daily) and `catalog.json`. The catalog records run
UTC time, columns and units, requested dates, source, sampling and null policies,
checksum and acquisition snapshot identity. Source chunks, receipts and staging
are private cache artifacts outside the published directory. No source credentials
are published. Repeated calls reuse checksummed local chunks. The cache pins its
metadata snapshot; use a fresh cache for a newer ERA5/ERA5T vintage. A snapshot is
not a guarantee that the remote objects were published simultaneously.

The implementation checks coordinates, units, time coverage and chunk layout;
missing objects and out-of-range dates fail without publishing a partial product.
Publication uses the toolkit's recoverable multi-file transaction. Ordinary readers
should verify the Parquet checksum in the catalog and retry during replacement.

## Usage and products

```python
from meteorology import get_data, get_metric

result = get_data("2009-01-01", "2009-01-31")
# result.hourly_path, result.daily_path, result.catalog_path
wind = get_metric("wind", "2009-01-01", "2009-01-31",
                  output_dir="data/weather-wind")
tmin = get_metric("TEMPERATURE_2M_C_MIN", "2009-01-01", "2009-01-31",
                  output_dir="data/weather-tmin")
```

The public directory contains exactly:

- `meteorology-hourly.parquet`: `H3_INDEX`, `DATETIME` (UTC hour start), metric columns.
- `meteorology-daily.parquet`: `H3_INDEX`, `DATE` (Arrow date32), metric columns.
- `catalog.json`: run date/time, date range, columns/types/units, checksums and compact source details.

Hourly temperature/dewpoint, U/V wind, wind speed/FROM direction, pressure,
cloud cover and RH are instantaneous at `DATETIME`. Precipitation is the total
in `[DATETIME, DATETIME + 1 hour]`, taken from the source's ending-hour timestamp.
Daily temperature has mean/min/max, wind U/V means, wind speed mean/max and
FROM direction of the mean vector, dewpoint/RH/cloud means, pressure mean/min,
and precipitation sum. Daily RH requires all 24 supported hourly derivations;
no clipping or partial-hour averaging is performed. The native quarter-degree
source is reduced before output; each row remains an H3 R5 sampling of that source.

`get_metric` accepts `temperature`, `dewpoint`, `relative_humidity`, `wind`,
`cloud_cover`, `pressure`, `precipitation`, or an exact column from the catalog.
An exact daily column includes its hourly counterpart; an exact hourly column
includes its corresponding daily aggregates. Other metric families are not fetched.
Both functions return `RegionalResult` with three file paths. They replace the
selected output set with the requested date range; they do not silently append
old ranges or preserve old metric columns. Use a separate `output_dir` for a
separate product. Unrelated files in an output directory cause an error.

Defaults: `output_dir="data/meteorology"`, private cache in sibling
`.meteorology-cache`, four download workers, 8 GiB transferred per call, maximum
one hour per call. `cache_dir`, `workers` (1–4), `transfer_bytes`, `max_seconds`
(up to one day), and `bbox` are keyword options. Cached objects are checksum
verified; incomplete arrays are rebuilt from completed source chunks on retry.
The transfer/time limits are per invocation; retry is an explicit new budget.
No automatic cache eviction is performed. Long hourly H3 exports can be much
larger than the native source archive; choose dates/output storage accordingly.
The implementation processes one day at a time and memory-maps regional source
blocks, instead of loading the full requested history into RAM.

Install the core package (now including `numcodecs`); no GRIB decoder is needed
for this API. Credentials use the existing `CDSAPI_KEY`, `CDSAPI_RC`, or
`~/.cdsapirc` resolver. Tokens go only to the fixed official ECMWF ARCO endpoint;
redirects and proxy forwarding are disabled. Cache reuse does not require a token
when all required source objects and metadata are already retained.

Regression checks cover UTC timestamp precision, source-block boundaries, rainfall
interval alignment, nulls, selected columns, cache corruption and publication failure.
The vectorized hourly exporter is compared with the reference reducer, including
unsupported humidity and calm wind. Provider compatibility and these software checks
do not establish independent observational accuracy. After changes, run
`python -m pytest -q tests/test_regional.py`, the full suite, lint, strict docs,
distribution and outside-checkout installed-wheel checks before live use.

Provider documentation:
[ECMWF ERA5 ARCO product guide](https://confluence.ecmwf.int/pages/viewpage.action?pageId=505390919).

## Full-area run, 2026-10-15

The installed API completed 2009-01-01 through 2026-10-08 for the coastal
bounding box and 1,761 H3 R5 cells. The store ended at 2026-10-09 23:00 UTC;
October 8 is the latest complete day with its following-midnight precipitation
sample. Published outputs contain 274,293,360 hourly rows (10 metric columns,
9,151,514,590 bytes) and 11,428,890 daily rows (14 metrics, 585,945,174 bytes).
The catalog is about 7 KB. The final cached export took 771.67 seconds with
zero provider requests. Source acquisition was resumed during validation and
optimization; this is not a cold end-to-end timing claim.

Checksums, every date/time/H3 key, row counts, null counts and seven selected
hourly-to-daily comparisons passed. Unsupported RH remains null: 56,504 hourly
rows and 48,121 daily rows. Other metrics had complete value support. These are
software/source consistency checks, not independent observational validation.
The final suite passed 541 tests, with the opt-in NOAA test skipped; the installed
wheel passed all 10 regional tests outside the checkout. Lint, strict docs,
distributions and dependency checks passed. See the
[machine-readable run evidence](evidence/ecmwf-regional-live-20261015.json).
