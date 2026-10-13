# One week of regional weather

This reproducible demo acquires January 1–7, 2024 HRRR f00 analyses, publishes
seven validated hourly R5 releases, then exports daily and calendar-week
statistics for **both native H3 cells and the whole configured region**.
It measures elapsed time, process peak memory, transferred payload bytes and
retained storage including evidence. This is retrospective weather context;
it does not establish future forecast skill or ecological predictive value.

[See the executed example and measured results](live-week-results.md).

## Source and processing contract

Source: [NOAA HRRR public archive](https://registry.opendata.aws/noaa-hrrr-pds/),
accessed through its public AWS HTTPS endpoint. Attribute NOAA/NCEP; consult
[NWS reuse terms](https://www.weather.gov/disclaimer/). The derived demo is not
an official NOAA product. Generated data stay outside tracked source.

The downloader selects the eight atmospheric GRIB messages by exact inventory
selectors, obtains bounded byte ranges, and runs the ordinary decoder's field,
unit, initialization/valid time, wind rotation and native-footprint checks.
It retains the index and selected GRIB bytes until decoding succeeds, then
keeps checksummed cropped grids and provenance. Hourly values are nearest
native-point samples at H3 R5 centroids, including land and water. Precipitation
and astronomy are excluded. Missing fields/hours fail the daily release.

The default region is the packaged example box (46.85–49.70°N,
125.80–121.60°W), not a qualified full Salish Sea domain. A bounds change must
use a new demo workspace. Statistics use equal centroid-hour weights; they are
not area-weighted regional measurements. See [summary semantics](weather-summaries.md).

Budget limits apply cumulatively across restarts. Before every HTTP request,
the run durably reserves one request and its maximum response payload bytes.
Index requests reserve 64 KiB; GRIB requests reserve exact byte-range lengths.
Failures and interruptions retain those reservations. No automatic retries,
redirects or provider fallback are made. Responses must honor byte ranges.
Counters describe HTTP response bodies, excluding protocol headers/TLS overhead;
they are not a network-interface traffic meter. A changed budget or request
configuration requires a new run directory. One writer owns a demo at a time.

## Run the example

Install the toolkit from its checkout with the acquisition extra:

```bash
python -m pip install '.[acquisition]'
meteorology demo-hourly-week --output /tmp/meteorology-live-week --dry-run
meteorology demo-hourly-week --output /tmp/meteorology-live-week
```

The preview makes no requests and creates no files. The ordinary week requires
168 source cycles and at most 1,512 HTTP requests before failures/restarts.
Defaults cap the full run at 1,600 requests and 4 GiB of reserved payload.
Actual selected-message sizes are discovered from the archive indexes; if a
limit is reached, the demo stops with an incomplete report. Do not interpret
an incomplete report as a published week. The GRIB messages cover the full
native CONUS grid before local cropping, so small output files do not imply
small downloads or small decoder memory.

Repeat the identical command to resume. Completed days are revalidated from
frozen releases; completed hours in the current day are validated against
acquisition receipts. Changed inputs, dates or limits require a fresh output
path. Successful decoding deletes only that cycle's temporary GRIB/index;
failed-cycle files, normalized source evidence and frozen releases remain.
The demo never removes existing scientific products or automatically prunes data.

The output directory contains:

| Path | Purpose |
| --- | --- |
| `REQUEST.json`, `CONFIG_HASHES.json` | Pinned dates, budgets and configuration |
| `TRANSFER.json` | Persistent request reservations and response-body counters |
| `workspace/retained-decoded/` | Cropped source grids, provenance and acquisition receipts |
| `releases/<date>/<release-id>/` | Independently validated, relocatable hourly daily releases |
| `summary-both/weather-summary.parquet` | Daily and weekly native-H3 plus region rows |
| `summary-region/weather-summary.parquet` | Region-only comparison of storage size |
| `REPORT.json` | Completion, checksums, elapsed time, process RSS and storage accounting |
| `REPORT.md` | Readable resource comparison, daily regional means and weekly H3 variation |

Both summary directories include a normal toolkit `MANIFEST.json`. Validate a
summary separately:

```bash
meteorology validate --manifest /tmp/meteorology-live-week/summary-both/MANIFEST.json
```

The week Parquet is long-form: period × spatial scope × H3 cell (null for region)
× metric, with mean/min/max, units, coverage and assumed availability. Use
`PERIOD='day'` or `PERIOD='week'` explicitly; never mix those rows in a mean.

```python
import pandas as pd

weather = pd.read_parquet('/tmp/meteorology-live-week/summary-both/weather-summary.parquet')
regional = weather.query("SPATIAL_SCOPE == 'region' and PERIOD == 'day'")
print(regional.pivot(index='LOCAL_START_DATE', columns='METRIC', values='SAMPLED_MEAN'))
weekly_h3 = weather.query("SPATIAL_SCOPE == 'h3' and PERIOD == 'week'")
```

For operational forecasting, select inputs available at the forecast issue
time. This retrospective example includes the complete observed period and
must not be used as though it were known at the start of that week.
`AVAILABLE_AT_UTC` is an assumed lag, not measured provider publication timing.

## Interpreting resource measurements

Report storage categories separately. `workspace_including_evidence` includes
normalized sources and mutable producer state; `frozen_releases_including_evidence`
includes copied source/support evidence as well as hourly values. The two
summary directories add storage. Their Parquet-size reduction does not imply
the archive itself shrank. No source-retention policy is enacted here.

Peak RSS covers the whole Python process, including decoder and validation
libraries. It is an operating-system high-water mark, not a sampled average.
Elapsed time includes validation and freezing. Across interrupted invocations,
reserved bytes remain an upper bound, while received-byte counters may omit
uncheckpointed bytes from an abruptly killed request. They exclude HTTP/TLS
and transport overhead. Keep the complete ledger with any benchmark report.
