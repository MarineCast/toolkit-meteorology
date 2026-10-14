# ERA5 historical backfill

## Request a decade

Install from the owning checkout with `python -m pip install '.[era5]'`.
The standalone API requires no OrcaCast imports, initialized workspace, daylight
or lunar products. ERA5 is the historical source; HRRR remains available through
the existing getters.

```python
from meteorology import backfill_weather

result = backfill_weather(
    start_date="2009-01-01", end_date="2018-12-31",
    bbox=(-129.7, 45.9, -121.5, 51.5),
    data_source="era5",
    variables=["temperature", "wind", "relative_humidity", "cloud_cover",
               "pressure", "precipitation", "dewpoint"],
    frequency="daily", spatial_output="native",
    cache_dir="./data/cache", output_dir="./data/meteorology",
    workers=2, resume=True,
)
print(result.metadata["result_id"])
for table in result.iter_tables():
    print(table.num_rows)  # ingest bounded Arrow batches downstream
```

```sh
meteorology backfill --start-date 2009-01-01 --end-date 2018-12-31 \
  --bbox -129.7 45.9 -121.5 51.5 --data-source era5 \
  --variables temperature wind relative_humidity cloud_cover pressure precipitation dewpoint \
  --frequency daily --spatial-output native --workers 2 \
  --cache-dir ./data/cache --output-dir ./data/meteorology --resume
```

The rectangle is illustrative atmospheric support, not an approved water mask.
Append `--dry-run`, or pass `dry_run=True`, for a read-only plan: exact monthly
selections, terminal hour, estimated grid size, jobs and resource limits. The
plan does not prove authenticated access or accepted terms.
`meteorology backfill --help` lists supported options.

Dates are inclusive UTC days within ERA5's declared coverage from 1940, with a
conservative recent-data lag. Recent products may contain ERA5T (expver 5).
The source decoder verifies actual coordinates and metadata; a provider response
does not itself prove complete hourly support.

## Credentials and terms

Follow the [official CDS API setup](https://cds.climate.copernicus.eu/how-to-api):
create or sign in to your Copernicus account, obtain the personal access token,
and save it in `~/.cdsapirc`:

```yaml
url: https://cds.climate.copernicus.eu/api
key: <your personal access token>
```

Manually accept the applicable terms on the
[ERA5 single-level dataset page](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels?tab=download).
The toolkit checks existing acceptance and never accepts terms on your behalf.
Credential resolution also supports `CDSAPI_KEY`, `CDSAPI_RC`, or an explicitly
selected env file containing `COPERNICUS_API_KEY=...`. The latter uses
`credential_env_file=` / `--credential-env-file`. Tokens, authenticated URLs
and provider error bodies are excluded from reports. Retain source attribution
and honor provider data rights in [sources and rights](DATA_SOURCES.md).

## Study-derived requests

```sh
meteorology backfill --study-config /path/to/study.v1.json --dry-run
```

The explicit study supplies its bbox and start date. An omitted end date is
clamped to the earlier of its inclusive end and today UTC minus six days,
allowing the terminal accumulation hour. Explicit dates must lie within the
study's declared time window. Its requested configuration and actual backfill
interval both remain in provenance. Proposed geometry can support planning and
an atmospheric rectangle companion; it cannot qualify a production marine
reporting release. With no directory overrides, outputs use the resolved Data
root's `meteorology/native/era5-backfill`, and cache uses
`meteorology/raw/era5-backfill`. Standalone defaults are `./meteorology-backfill`
and `./weather-cache`.

## Batching, limits and recovery

Monthly requests combine input dependencies: RH and dewpoint reuse temperature/
dewpoint fields; wind shares U/V. Partial endpoint months are exact selections.
Accumulation metrics use the next chunk's first midnight, plus one small terminal
00:00 request. No separate boundary job is issued for every day or internal month.

Planning recursively splits temporal chunks to fit the 120,000-field limit,
conservative source-byte estimate and RAM budget. Explicit provider HTTP 413 or
oversized delivered payloads also create a durable split record and smaller child
requests. The rejected parent, its remote ID when known, partial bytes and charged
counters stay available. A single oversized day requires a smaller extent or
explicitly reviewed larger caps; it is not silently broadened. Bboxes retain the
short planner's bounded regional crop admission (at most 4,096 halo grid points).

| Resource | Python option / CLI | Default |
| --- | --- | --- |
| Workers | `workers` / `--workers` | 2; allowed 1–4 |
| Measured process RAM | `memory_bytes` / `--memory-mib` | 1 GiB |
| Aggregate active cache staging | `staging_bytes` / `--staging-mib` | 2 GiB |
| Raw source chunk cap | `source_chunk_bytes` / `--source-chunk-mib` | 64 MiB |
| Aggregate reserved transfer | `transfer_bytes` / `--transfer-mib` | 64 GiB |
| Aggregate HTTP calls | `max_requests` / `--max-requests` | 20,000 |
| Charged elapsed execution | `max_seconds` / `--max-seconds` | 48 hours; maximum 30 days |
| Retained cache quota | `retained_cache_bytes` / `--retained-cache-mib` | Unbounded; no eviction |
| Transient retries | `retries` / `--retries` | 3; allowed 0–6 |

Transferred bytes and full pre-open reservations are distinct. Retries remain
charged even if their payload fails. Storage is admitted before a new provider
submission. Physical cache/output disk space is checked too. The optional retained
cache quota covers the cache namespace; published outputs have a separate
destination and must also have sufficient disk. Existing cache/published evidence
is never automatically evicted. Full cache traversal occurs only at worker-budget
startup, not per GRIB message, block or source chunk.

Start with two workers and daily native output. Raising workers increases
simultaneous cubes and must pass RAM admission. A default full coastal month has
about 39 MB of numeric hourly values for the seven primary source parameters;
actual RSS includes Python/codec/Parquet and overlapping worker allocations.
Use measured receipts instead of assuming the numeric cube equals total RAM.

Restart the identical request with the same cache, output, workers and original
resource caps. Valid downloaded sources and completed partitions are reused.
Known remote IDs resume polling/downloads; transient failures use bounded
exponential delays. Interrupted downloads retry into new immutable attempt paths.
An unknown submission outcome fails explicitly and requires CDS reconciliation;
the executor never blindly posts it again. Source aliases reuse a verified
same-area/month selection containing all requested days, hours and parameters.
This relaxed subset reuse is limited to the new backfill path; exact-source guards
of existing getters remain unchanged.

`PROGRESS.json` is operational progress. Signed `CURRENT.json` partition pointers,
immutable companion manifests and checksum-valid sources determine completed
work after a crash. Corrupt derived artifacts trigger selective repair; their old
bytes remain. Invalid signed records fail closed for explicit reconciliation.
No counter-reset or cache-deletion command accompanies this API.

Hourly normalization is bounded per admitted chunk and normally removed only
after its daily partition commits. `retain_intermediates=True` /
`--retain-intermediates` keeps it; raw GRIB and source receipts are always retained.
Failed scratch remains available for inspection. Native daily reduction precedes
H3 expansion. Output is compressed Zstandard Parquet in year-month/chunk
directories; one day/metric is written at a time. The whole historical table is
never assembled by the executor.

## Daily and hourly features

| Variable | Daily metrics | Units |
| --- | --- | --- |
| `temperature` | `TEMPERATURE_2M_C_MEAN`, `_MIN`, `_MAX` | degC |
| `wind` | U/V `_MEAN`; speed `_MEAN`, `_MAX`; direction `_MEAN_VECTOR` | m/s; degrees FROM |
| `relative_humidity` | `RELATIVE_HUMIDITY_2M_PCT_MEAN` | percent |
| `cloud_cover` | `TOTAL_CLOUD_COVER_PCT_MEAN` | percent |
| `pressure` | `MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN`, `_MIN` | hPa |
| `precipitation` | `PRECIPITATION_MM` | mm accumulated over UTC day |
| `dewpoint` | `DEWPOINT_2M_C_MEAN` | degC |
| `snowfall` (optional) | `SNOWFALL_WATER_EQUIVALENT_MM` | mm water equivalent |

Temperature/dewpoint convert K to degC, pressure Pa to hPa, cloud fraction to
percent, and tp/sf metres to millimetres. RH uses the existing Bolton saturation
formula over water and its supported temperature/dewpoint domain. Wind speed
averages hourly magnitudes, not the magnitude of mean U/V. Direction is FROM
the daily mean earth-relative vector; a calm mean has a null value and
`VALUE_STATUS=UNDEFINED_CALM_MEAN`, with complete input counts retained.

Instantaneous daily quantities require hours 00:00–23:00. Accumulations require
validated one-hour intervals ending 01:00–next-day 00:00. Missing inputs make the
affected metric null with `PARTIAL`/`UNAVAILABLE`, never a zero or a partial sum.
Valid counts use the joint dependencies for derived metrics. Source final expver
1 takes precedence over preliminary 5 even if the final value is missing.
Visibility/fog are unsupported; this API invents no proxy.

`frequency="hourly"` / `--frequency hourly` emits instantaneous names and hourly
interval amounts with `VALID_TIME_UTC` and counts out of one. Accumulation hours
are assigned to their supporting UTC day, including next midnight. Hourly output
has substantially more rows; it does not bypass chunk/RAM limits.

## Native identity, H3 and provenance

Native rows use nullable `H3_INDEX` with `SOURCE_GRID_HASH`, `SOURCE_GRID_INDEX`,
`SOURCE_LAT` and `SOURCE_LON`. Only grid points inside the requested rectangle
are emitted; acquisition includes a small quarter-degree sampling halo.
`spatial_output="h3"` samples R5 centroids with a haversine nearest-grid crosswalk
calculated from the verified decoded coordinates. Its cached record binds bbox,
grid, H3 cells, indices, distances and method. Centroids must lie within the actual
grid footprint and within a regular-cell half-diagonal distance allowance.
This geometrically validated mapping does not establish independent regional
accuracy, a marine membership mask or additional atmospheric resolution.

Every row retains UTC interval, metric/method/schema, source resolution,
`VALID_HOURS`, `EXPECTED_HOURS`, `COVERAGE_FRACTION`, `CONSOLIDATION_STATUS`,
`SOURCE_LINEAGE_ID` and a per-parameter missing-slot bitmap. The immutable
partition JSON resolves shared lineage to source SHA, retrieval time, valid/init
time, interval start, lead, grid and selected expver. `AVAILABLE_AT_UTC` remains
null: acquisition time cannot establish historical forecast availability.

The result's `metadata` exposes committed partition paths, IDs, source receipts,
resource counters, current execution timings and qualification flags. Final-cache
hits make no provider calls or GRIB decodes. `iter_tables()` verifies evidence and
streams Arrow batches of at most 4,096 rows. `to_pandas(max_rows=1000000)` rejects
larger materialization. `to_parquet(path)` publishes a streamed export without
overwriting existing files; keep the partition manifests/raw cache for lineage.
Consumers must join explicit UTC dates and spatial support, never mix native
grid and H3 identity silently.

## Backend and performance evidence

The [performance report](era5-backfill-performance.md) separates generated-GRIB
software measurements, actual ARCO transfers and unrun CDS/provider comparisons.
CDS retains the qualified strict GRIB decoder; ARCO is not exposed as an execution
backend until representation, accumulation and revision equivalence are tested.
The under-48-hour decade estimate is a conditional engineering target, not a
measured production guarantee.

## Baseline audit before implementation

Optimization starts at `b59567f`, retaining the lifecycle fixes on PR 13 while
fetched main remains `368ddfc`. Branch: `codex/era5-historical-backfill`.

- `request.py` limits requests to seven UTC days, creates one regional CDS job
  per day, and separately requests each next-midnight precipitation/snowfall hour.
  A ten-year complete-variable request therefore needs approximately 7,300 jobs.
- `request_adapters.era5_day` waits for every job serially. Each day constructs a
  provider and verifies terms. Exact source-selection guards prevent arbitrary
  substitution of a differently selected retained source.
- `request_cache.execute_request` owns the namespace lock throughout acquisition,
  preventing independent date chunks in that namespace from overlapping network work.
- Normalization is streamed once per day to point-block Parquet, then read back
  for daily reductions. This avoids whole-history RAM, but retains hourly
  intermediates and performs repeated setup/grid and Parquet work across days.
- Existing legacy `era5.planning.request_plan` already groups exact month/day
  selections and adds only a terminal boundary. Its logic is reusable; legacy
  pilot resource and publication contracts remain unchanged.
- Existing getters preserve metric/hour lineage, expver selection, missingness,
  UTC accumulation support and immutable source/result evidence. These are parity
  requirements, not disposable optimization overhead.

Full-envelope generated baseline profiling found an additional CPU defect: a
two-day run remained in its first day's reduction for over twenty minutes after
normalization, using 100% CPU. A bounded four-point reproduction with a large
forecast-support catalog spent 0.390/0.425 seconds in pandas attribute finalization
and `copy.deepcopy`. Every scalar lineage lookup copied that catalog. The executor
already captures forecast support separately; reduction now uses a shallow data
copy with empty local attrs, preserving the caller catalog and all scientific rows.
The interrupted original baseline files/journals remain available locally.

## New product contract

ERA5 CDS single-level reanalysis is the initial authoritative backend, subject to
manually accepted CDS terms and attribution. ARCO is evaluated independently;
its grid, available variables, accumulation representation and source revision
must be validated before adoption. No ocean, seascape or astronomy inputs.

Daily products use UTC midnight intervals and native 0.25-degree distribution-grid
identity. Requested metrics have valid/expected counts and coverage; incomplete
metrics are null, never inferred zero. Wind-speed mean averages hourly magnitudes;
direction is meteorological FROM direction of the mean earth-relative vector,
undefined for a calm mean. Relative humidity retains the existing Bolton formula.
Precipitation/snowfall sum verified one-hour intervals ending 01:00 through the next
00:00. Source expver and metric-specific lineage remain explicit.

Optional H3 R5 output is nearest native-grid sampling, with a cached validated
crosswalk and source index/distance retained. H3 mapping adds no atmospheric
resolution. Rectangle support does not certify the pending MarineCast water mask.
New backfill method/schema identify this expanded daily feature product; existing
getters, HRRR, legacy products and immutable releases retain their contracts.

Monthly source jobs and small terminal boundary jobs are immutable, independently
locked and resumable. Output publication uses short leases. Plans/resource caps,
charged attempts, source checksums and completed partition receipts persist across
restart. Default retention keeps compact raw sources and daily partitions;
hourly normalized intermediates are optional. Existing evidence is never deleted.

## Qualification state

CDS credentials are absent from the current environment. Live two-day/month/
three-month and numerical backend comparisons remain pending provider access.
Software fixtures and metadata inspection cannot establish production regional
accuracy or the engineering target of a ten-year run under 48 hours.
