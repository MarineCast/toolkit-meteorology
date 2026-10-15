# Weather requests

For multi-year ERA5 intervals use the separate
[historical backfill interface](era5-backfill.md). The bounded getters described
here retain their seven-day, source-support and publication contracts.

Install from a clone with Python 3.11–3.13 on macOS/Linux:

```sh
python -m pip install '.[native-pilot]'  # direct selective HRRR GRIB requests
python -m pip install '.[era5]'          # ERA5 and its GRIB codec
```

The core package needs neither Folium, Branca nor GeoPandas. HTML map inspectors
require `.[visualization]`; legacy Herbie workflows require `.[acquisition]`.
These commands install local source. This task does not establish PyPI publication.

## Python and CLI

```python
from meteorology import get_temperature, get_weather

result = get_temperature("2024-01-01", "2024-01-01",
                         (-124.0, 48.0, -123.0, 48.5), "hrrr",
                         cache_dir="./weather-cache")
df = result.to_pandas()
result.to_parquet("temperature.parquet")
print(result.metadata)

mixed = get_weather(variables=["temperature", "wind", "cloud_cover"],
                    start_date="2024-01-01", end_date="2024-01-01",
                    bbox=(-124.0, 48.0, -123.0, 48.5), data_source="hrrr",
                    frequency="daily", h3_resolution=5, cache_dir="./weather-cache")
```

```sh
meteorology sources
meteorology variables --data-source era5
meteorology get temperature --start-date 2024-01-01 --end-date 2024-01-01 \
  --bbox -124 48 -123 48.5 --data-source hrrr --cache-dir ./weather-cache \
  --output temperature.parquet
meteorology get weather --variables temperature wind cloud_cover \
  --start-date 2024-01-01 --end-date 2024-01-01 --bbox -124 48 -123 48.5 \
  --data-source hrrr --dry-run
```

These are bounded **real acquisition examples**, not a claim of live execution in
this refactor. Preview them first. Dry run creates no directories, reads no secret
files, and makes no provider requests. It reports exact boundaries, dependencies,
output support, final-cache hits, missing work, request structure and caps. Transfer
sizes remain unknown. Resource caps are upper limits, not predicted payload sizes.

## Software support matrix

All supported combinations offer daily and hourly long output. Source compatibility
is tested with generated GRIB and mocked HTTP; authenticated CDS, new live NOAA
requests and independent regional accuracy are unverified in this refactor.

| Getter / quantity | HRRR | ERA5 | Units and daily reduction |
| --- | --- | --- | --- |
| `get_temperature` / 2 m air temperature | R5 or native | Native | degC, mean |
| `get_wind` / 10 m U, V and speed | R5 or native | Native | m/s, mean of each hourly quantity |
| `get_cloud_cover` / total cloud cover | R5 or native | Native | percent, mean |
| `get_pressure` / mean sea-level pressure | R5 or native, NOAA MSLMA | Native | hPa, mean |
| `get_relative_humidity` / 2 m RH | R5 or native, direct | Native, Bolton water approximation from T/Td | percent, mean |
| `get_dewpoint` / 2 m dewpoint | Unsupported | Native | degC, mean |
| `get_visibility` / surface visibility | R5 or native | Unsupported | km, mean |
| `get_precipitation`, `get_precip` | Unsupported accumulation | Native | mm, sum |
| `get_snowfall` / interval snowfall | Unsupported | Native | mm water equivalent, sum |

HRRR defaults to R5 centroid support, including land and water. Choose
`spatial_output="native"` / `--spatial-output native` to retain original grid
indices at points inside the bbox. Native point identity includes the source grid
hash. ERA5 defaults to its native CDS distribution grid and rejects H3 requests
before acquisition. H3 sampling for ERA5 remains unqualified; duplicating values
at finer cells would not add physical detail.

## Time, space and missingness

Bbox is WGS84 `(west, south, east, north)`; longitude wraps/dateline rectangles
are unsupported. Date-only endpoints are inclusive, normalized to a UTC interval
ending at midnight after `end_date`. Daily requests use actual 00–23 UTC samples
for instantaneous quantities. Means stay null unless all 24 slots are finite;
rows carry `STATUS`, `VALID_HOURS`, `EXPECTED_HOURS` and `COVERAGE_FRACTION`.
Hourly accumulation rows label the interval's **end**; the last interval ends at
next-day midnight, while `DATE` labels its requested UTC day.

Daily wind speed is the mean of hourly magnitudes; it differs from the magnitude
of the mean U/V vector. HRRR grid-relative winds use the existing geodesic axis
rotation and must preserve vector magnitude. U/V flags, every message's grid
identity, decoded native footprint and nearest-distance allowance are verified.
ERA5 winds must declare earth-relative orientation. Grid order/crop/rotation and
crosswalk reuse bind the source grid, target support and spatial method.

HRRR requests are limited to the v4 CONUS decoder era starting 2020-12-03 and a
conservative CONUS envelope; actual decoded coverage is still checked. Earlier
HRRR eras remain available only through separate existing workflows where
qualified. ERA5 begins in 1940, with a conservative five-day recent-data exclusion;
that calendar bound does not guarantee provider availability. Requests are limited
to seven days, 4096 native crop points, H3 R5 where supported, and one worker.
The memory/transfer/staging/time caps can be supplied with `Limits` or CLI flags.
Memory admission uses current RSS plus planned allocation; historical peak RSS
is reported separately. Enforcement is cooperative, not OS preemption. Full HRRR
messages are nationwide even for a small bbox; only selected fields are acquired.

## Precipitation and snow

`get_precip` is exactly `get_precipitation`. ERA5 CDS single-level `tp` and `sf`
are already one-hour accumulations ending at valid time. Daily totals use 01–23 UTC
and the following midnight. The adapter requests that boundary, validates forecast
initialization and one-hour step bounds, and rejects cumulative/overlapping,
ERA5-Land and zero-length fields. No deaccumulation is performed. A complete dry
input produces zero; missing input produces a null with explicit coverage.

ERA5 snowfall is official parameter **144 (`sf`)**, delivered in metres of water
equivalent and converted by 1000 to millimetres of water equivalent. It is falling
snow accumulated during an interval. It is distinct from ground snow depth and
snowpack water equivalent; no fixed snow-to-water ratio is applied. See the
[ECMWF parameter definition](https://codes.ecmwf.int/grib/param-db/?id=144) and
[ERA5 accumulation documentation](https://confluence.ecmwf.int/pages/viewpage.action?pageId=388500357).
This is implemented source normalization with fixture qualification; actual CDS
snowfall payload compatibility and regional scientific acceptance remain unrun.

The legacy `PRECIP_MM_DAY_ESTIMATE` remains six matched f01 **rate snapshots**
multiplied by four hours. It is not the new interval accumulation. Use the existing
`download_surface_weather` / `build_surface_weather` for that complete family.
`get_precipitation_estimate` raises a migration message: a selective rate-estimate
adapter has not been implemented and is not advertised as supported.

## Results, caching and provenance

`WeatherResult` is disk-backed. `.to_pandas()` explicitly materializes its selected
rows; construction does not load a historical request. `.to_parquet(path)` streams
4096-row batches into an atomic export and embeds JSON under the Arrow metadata
key `meteorology.manifest`. Metadata survives Parquet serialization. Each result
has an immutable checksum-backed manifest and date/metric partitions.

Daily H3 keys are `DATE × H3_INDEX × METRIC`; native keys include
`DATE × SOURCE_GRID_HASH × SOURCE_GRID_INDEX × METRIC`. Hourly keys additionally
include `VALID_TIME_UTC`. Columns retain value, units, source, status/coverage,
source coordinates/grid, spatial sampling distance, interval, method, checksums,
retrieval-as-of and consolidation. Durable metadata additionally captures the
actual source forecast initializations/leads and parameter/valid-time/checksum
bindings, plus software and scientific dependency versions. Unknown historical publication time stays null;
retrieval time is not substituted for it.

Compatible repeat requests use the final cache without creating a provider client,
checking remote terms or making any provider requests. A variable/date subset
reads only its selected result partitions. Source byte caches are shared across
getters; HRRR indexes and already selected fields are reused when another variable
is requested. ERA5 job identities bind exact provider selections and preserve
resumable job state. Variable order does not change logical identity. Derived IDs
bind source receipts, grids, variables, bounds, periods, timezone, reductions,
resolution and method/schema/producer fingerprints. Cache corruption raises an
error; incomplete results are returned with partial status and never treated as
successful final hits. `refresh=True` creates a new source generation and preserves
prior evidence. There is no incidental archive cleanup.

A namespace lease serializes concurrent writers; a waiting compatible caller reads
the first completed result. Persistent transfer counters charge interrupted
attempts. Existing limits must be retained for resumption. Raw receipts and final
artifacts are immutable; failed stages remain inspectable. The 2 GiB staging cap applies to each run
and its in-progress writes. Retained evidence has a separate optional namespace
quota and an independent physical-disk safeguard; there is no automatic eviction.

## ERA5 credentials

Both planning and execution use one resolver. Precedence:

1. Explicit `credential_env_file` / `--credential-env-file` containing one
   `COPERNICUS_API_KEY` alias.
2. `CDSAPI_KEY`, with optional `CDSAPI_URL`.
3. `CDSAPI_RC`, otherwise `~/.cdsapirc`, containing `key` and `url`.

Only `https://cds.climate.copernicus.eu/api` is supported. Plans report configuration
presence separately from authenticated access, which is not checked offline.
Tokens are not logged or serialized. Accept dataset terms manually using the
[official CDS setup](https://cds.climate.copernicus.eu/how-to-api) and
[download form](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels?tab=download).
The bounded provider verifies accepted terms; it never accepts them automatically.
No credential is needed for a valid final-result cache hit.

## Offline example and common errors

```sh
python -m pip install '.[era5]'
python demo/request_offline.py --output ./request-demo
```

The clone's demo writes small generated GRIB, invokes the same public getter and
native adapter, and exports explicitly synthetic temperature and snowfall. It
requires no credentials or network. Advanced `source_files`/`retrieved_at_utc`
options always label inputs synthetic and use a separate `fixtures` cache; they
are not a route for certifying captured provider archives.

| Error | Action |
| --- | --- |
| Unsupported source/variable | Check `meteorology variables --data-source SOURCE`; choose an implemented source explicitly |
| No native points / empty H3 support | Enlarge the bbox; do not invent a representative point |
| Outside source era/envelope or wrapping bbox | Use a supported date/rectangle; ERA5 is not substituted for HRRR |
| Conservative working set/crop exceeds limits | Reduce the bbox or interval; check declared caps before acquisition |
| Transfer/request/time cap exceeded | Inspect retained run state; resume with original caps or start a new bounded run |
| Credentials absent / terms unaccepted | Configure the official CDS mechanism and manually accept terms |
| Corrupt result/source receipt | Inspect retained files; use explicit refresh for a new generation |
| Partial day | Inspect missing/bitmap slots; values remain null and status does not certify completeness |

## Compatibility and publication

Existing family builders, CLI commands, local-day/DST calculations, immutable
releases and study/marine-mask publication gates are preserved. No process-global
environment variable is modified by a getter, and getters do not invoke the CLI.
A generic bbox query ignores legacy workspace/study environment selection and
never claims a production shared-study release. Use the existing shared-study
workflow for that publication; its approval/registry prerequisites remain intact.
The request method/schema are independent v2 contracts; old releases are not
migrated or rewritten. Existing fixed pilots remain regression/qualification cases.

## Source vintage and explicit pins

A complete refresh commits an atomic active-generation index after validating its
manifest, artifacts and source receipts. The index binds each source parameter and
UTC date within a source/bbox/evidence namespace. Overlapping refreshes replace
only their covered parameter/date keys. Hourly and daily representations share the
same source vintage; reprocessing retained bytes never advances it. Representation
size is considered only after selecting that vintage. Immutable index snapshots
and old results remain retained.

A request spanning different active generations (including refreshed and previously
unrefreshed dependencies/dates) requires explicit resolution: refresh the entire
requested coverage or pin a coherent historical result. There is no automatic
composition or downgrade to an older superset. An active refreshed ERA5 vintage
that lacks a new exact provider selection also requires explicit resolution;
acquiring a new subset job cannot masquerade as reprocessing that vintage. Failed and partial refreshes retain
the prior active generation. Readers and writers share a namespace lease. Corrupt
active evidence raises an error; a successful refresh creates a healthy generation
without deleting the corrupt evidence.

Use `result_id=previous.metadata["result_id"]` with the same source, bbox, frequency
and compatible date/variable subset to read a historical result explicitly. Pins
validate all source receipts and selected artifacts and retain the historical
method/schema. A pin can locate retained synthetic results without re-supplying
fixture files and still labels them synthetic. `refresh` and pinning are mutually
exclusive. Legacy manifests have
no inferred chronology: ambiguous eligible unindexed results require a pin or
refresh, and their contents are never rewritten.

## v2 metric and slot evidence

The v2 request method/schema adds `SOURCE_DEPENDENCIES` and `SOURCE_VALUE_STATUS`.
Dependencies retain point, valid time, parameter, selected expver/generation,
retrieval timestamp, source checksum, initialization, lead and accumulation start.
Each hourly metric uses only its own slot and dependencies. Wind speed uses U/V;
ERA5 derived RH uses temperature/dewpoint. Rotated HRRR components use both raw
components. Daily evidence aggregates only the 24 contributing slots; sums use
01:00 through the next midnight, excluding the preceding midnight interval.

A preliminary contributor is visible in the corresponding hour and daily status.
An absent message has no observation receipt or retrieval timestamp; a bitmap-null
value retains its actual source receipt with `BITMAP_NULL`. Both stay null with
explicit coverage. Final missing values never borrow preliminary values. Parquet
exports retain this evidence. Cache subset metadata filters forecast support to
selected metrics and slots. v1 artifacts cannot satisfy a v2 cache lookup; explicit
v1 pins remain readable with their historical identity and null v2 evidence fields.

## Source-cache-aware preflight

Dry runs distinguish the nominal cold-cache acquisition estimate, validated reusable
source objects, missing source work, remaining request lower bounds and transfer
reservations. A missing final representation can still require zero acquisition.
Exact HRRR ranges are planned from qualified retained indexes; absent indexes leave
field sizes unknown until the index is fetched and validated. CDS jobs remain keyed
by their exact provider selection and source-generation context. Terms/catalogue,
submission, polling, discovery, retries and downloads consume persistent request
and transfer reservations before network calls. Polling counts are unknown in advance.

Limits apply to remaining acquisition plus previously consumed run reservations.
Warm-source processing requires neither credentials nor a provider client; final
hits also skip decoding. Interrupted reservations cannot be reset by rerunning the
same logical request with different caps. A cold estimate is never treated as an
exact required count. Corrupt retained objects fail before reuse.

## Run staging, retention and physical disk

Request budgets reconcile the namespace once at run start/resume, then maintain
atomic per-write reservations and byte deltas. Per-run staging includes active
normalization, result stages, failed partials for that run, control allowance and
in-progress source downloads even when written under shared source-cache paths.
Committed objects become retained without double counting. Successful historical
cache entries do not consume a new run's staging allowance. Failed attempts remain
inspectable and interrupted transfer reservations stay charged.

Retention defaults to **unbounded, with no eviction**. Set `retained_cache_bytes`
in Python or `--retained-cache-mib` in the CLI to establish a persistent namespace
quota, covering retained evidence, in-progress growth and accounting metadata.
Subsequent requests inherit that quota; a different explicit quota is rejected.
Physical disk admission reserves writes plus a 16 MiB safety margin independently
of both caps. CDS source writes conservatively reserve their 32 MiB download cap
until the actual received size is committed; small quotas may reject even a small
fixture-sized response. Resource checks remain cooperative and do not promise OS
preemption inside native allocations. Existing fixed-pilot budgets retain their
original aggregate sibling-input/staging semantics.

```python
from meteorology import get_temperature
from meteorology.era5.resources import Limits

common = dict(cache_dir="weather-cache", spatial_output="native")
old = get_temperature("2024-01-01", "2024-01-01", bbox, "era5",
                      frequency="hourly", **common)
new = get_temperature("2024-01-01", "2024-01-01", bbox, "era5",
                      frequency="hourly", refresh=True, **common)
pinned = get_temperature("2024-01-01", "2024-01-01", bbox, "era5",
                         frequency="hourly", result_id=old.metadata["result_id"], **common)
# The exact selected source job is already retained: no credentials/provider I/O.
daily = get_temperature("2024-01-01", "2024-01-01", bbox, "era5",
                        limits=Limits(memory_bytes=1024**3, requests=1), **common)
assert daily.metadata["budget"]["requests"] == 0
```

Refresh performs acquisition; the generated-GRIB regression fixtures demonstrate
these paths offline. Neither the example nor passing software checks certify
regional accuracy, new geographic support or production backfill readiness.


## Execution lifecycle and recovery

A logical request identifies variables, dates, support and producer fingerprint.
The selected source generation identifies the immutable source vintage. An
execution ID binds that logical request to that generation. A different generation
gets a different run directory and fresh execution counters; retrying an unfinished
execution reuses its directory, original limits, consumed requests, transfer
reservations and compute charges. Interrupted refreshes have a durable profile
mapping to the same generation/execution, so another `refresh=True` resumes them
instead of silently assigning a new budget. A completed or partial terminal refresh
allows a later explicit refresh to start a new vintage.

`STORAGE_LEDGER.json` is operational format v2. Explicitly retained run paths have
a checksum-backed `RETAINED_STORAGE.json` record; reconciliation leaves those
paths retained while charging them to namespace/quota totals. Unfinished tracked
paths and shared-source reservations remain charged. Reconciliation scans once
at startup/resume; writes update counters incrementally. Legacy unclassified paths
stay conservatively charged, and legacy unfinished runs require explicit owner
reconciliation rather than guessed completion or discarded counters.

Lifecycle records and refresh profiles are checksum-backed format v2 and bind
request, generation and execution identities. Scientific method/schema remain
v2. The existing producer fingerprint includes changed operational code, so older
derived representations may miss the current cache and reaggregate from compatible
retained source bytes. Historical result pins and immutable v1/v2 artifacts remain
readable; no old scientific manifest is rewritten. ERA5 exact-selection and
mixed-generation guards remain in force.

## Active-pointer commit protocol

Validated result artifacts and immutable index history are written first. Storage
and commit-receipt reservations precede pointer replacement. The active index is
replaced atomically; successful directory fsync confirms durable publication. A
versioned `REQUEST_PUBLICATION.json` intent/receipt and writer witness distinguish:

| Outcome | API behavior |
| --- | --- |
| Failure before pointer replacement | Previous generation remains authoritative; the original failure propagates. |
| Confirmed durable pointer, then accounting/maintenance failure | Return the committed result with `publication_status=COMMITTED_WITH_OPERATIONAL_WARNING` and `operational_warnings` in result/export/CLI metadata. |
| Visible replacement without established durability | Raise `PublicationUncertainError`; require reconciliation and do not label it unpublished or definitely successful. |
| KeyboardInterrupt/SystemExit or process exit | Preserve interruption; restart validates durable intent and evidence before confirming/repairing state. |

Operational warnings do not alter immutable scientific manifests. Secondary
error-state writes attach diagnostic notes rather than replacing the original
exception. A confirmed commit is never rolled back automatically. Unresolved
committed accounting blocks a new execution while allowing validated cached reads
with warnings, preserving the pending commit evidence.

A normal getter reconciles pending state under the namespace lease before reading
or processing. `result.reconcile()` explicitly retries that bounded repair.
Recovery validates referenced artifacts, confirms pointer durability by fsync,
repairs only known completed control reservations and retention accounting, and
leaves acquisition journals/counters unchanged. It is idempotent and makes no
provider calls. Corrupt scientific evidence fails closed; persistent disk/ledger
problems remain actionable warnings or reconciliation errors. A dry run reports
uncertain durability instead of treating it as an ordinary cache hit. Explicit old
result pins remain available for inspecting historical evidence.

## Opt-in qualification through public getters

`demo/qualify_requests.py` uses `get_weather`, native support and one exact UTC day.
Its default is offline planning. Caps are explicit: memory 1 GiB, staging 2 GiB,
transfer 512 MiB, elapsed 1800 seconds; HRRR has 128 request reservations and ERA5
32. Retries/polling remain charged. No case, bbox or cap is widened automatically.

For HRRR temperature/vector wind and ERA5 temperature, create a case JSON with
`date` and `bbox`. For example, a compatibility-only plan can use:

```json
{"date":"2024-01-01","bbox":[-125.25,49.0,-125.0,49.25]}
```

This date is a compatibility example, not a wet/snowy reference case. Amounts
qualification requires a different, independently vetted historical case selected
by the operator. Retain its reference file next to the JSON and include:

```json
{"date":"<verified UTC date>","bbox":["<west>","<south>","<east>","<north>"],
 "independent_reference":{"file":"reference-evidence.ext","sha256":"<actual checksum>",
 "uri":"<independent authoritative source>","verification_statement":"<how nonzero tp and sf were verified>",
 "verified_nonzero_parameters":["tp","sf"],"date":"<same verified UTC date>",
 "bbox":["<same west>","<same south>","<same east>","<same north>"]}}
```

Replace placeholders with actual numeric bounds and independently reviewed evidence.
The checksum and matching support protect supplied provenance; a human statement
is a prerequisite, not automatic scientific certification. Do not guess a snowy
case, treat synthetic guard fixtures as observations, or retry a zero result at a
wider support. CDS credentials and manually accepted terms must already be configured;
the script neither prints secrets nor accepts terms.

```sh
# Offline dry runs; compatibility.json and verified-amounts.json are operator inputs.
python demo/qualify_requests.py --source hrrr --case /path/compatibility.json --cache-dir /path/private/hrrr
python demo/qualify_requests.py --source era5-temperature --case /path/compatibility.json --cache-dir /path/private/era5-temperature
python demo/qualify_requests.py --source era5-amounts --case /path/verified-amounts.json --cache-dir /path/private/era5-amounts
# Explicit provider opt-in after reviewing each unchanged plan:
python demo/qualify_requests.py --source hrrr --case /path/compatibility.json --cache-dir /path/private/hrrr --run
python demo/qualify_requests.py --source era5-temperature --case /path/compatibility.json --cache-dir /path/private/era5-temperature --run
python demo/qualify_requests.py --source era5-amounts --case /path/verified-amounts.json --cache-dir /path/private/era5-amounts --run
```

The procedure checks units, complete UTC slots/accumulation boundaries, native grid
identity, initialization/lead and receipts, vector/magnitude identity, nonzero
amounts, and a repeat cache hit. Exports use unique result-ID names and never replace
existing qualification evidence. Software regressions, exact-case provider
compatibility and independent regional scientific accuracy remain separate gates;
passing software checks establishes neither of the latter two. These live commands
were not executed as part of the lifecycle fix.
