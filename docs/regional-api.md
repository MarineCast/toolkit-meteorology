# Coastal weather API

## Approved coastal API

Install the core package with `python -m pip install .` using Python 3.11–3.13
on macOS/Linux. Set `MARINECAST_STUDY_CONFIG=/path/to/config/study.v1.json`, or
pass `study_config=`. The approved owner registry/mask are external inputs,
verified before acquisition; there is no implicit workspace path or generated membership.

```python
from meteorology import get_weather, ALLOWED_VARIABLES, check_weather

result = get_weather("2009-01-01", "2009-01-02", ALLOWED_VARIABLES,
    study_config="/path/to/config/study.v1.json",
    output_dir="scratch/weather", cache_dir="/path/to/source-cache")
check_weather(result.catalog_path, study_config="/path/to/config/study.v1.json")
```

Allowed families: `temperature`, `dewpoint`, `relative_humidity`, `wind`,
`cloud_cover`, `pressure`, `precipitation`. Omit the list to use configured METRICS, or all families when unset.
Unknown, duplicate and empty lists reject before source access. Only selected
source parameters are fetched. Dates are inclusive UTC dates inside the approved
study. Output is the selected approved registry membership, without an intermediate rectangle export.

## Local run defaults

All three producers read the current directory's `.env`, or `--env-file PATH`
(`env_file=PATH` in Python). No parent directory is searched. Explicit arguments
override process environment, then the file, then defaults. The legacy `DATA_DIR`
name remains supported. Within one source, `OUTPUT_DIRECTORY` wins when both
are set; environment values still override file values regardless of spelling. Values are parsed
without shell execution or environment mutation. Quote values containing spaces.

Configure `MIN_LAT`, `MAX_LAT`, `MIN_LON`, `MAX_LON`, `START_DATE`,
`END_DATE`, `REBUILD`, `OUTPUT_DIRECTORY`, `MARINECAST_STUDY_CONFIG` and
`CDSAPI_KEY`; see the repository's `.env.example`. Optional `VERBOSITY`
defaults to false and `METRICS` to all Weather families. Weather requires both
dates. Without configured dates, Solar Cycle uses the study range; Radiation
uses study start through actual complete source coverage.

Bounds must be supplied together, finite and ordered, inside the approved study
envelope. They select existing coastal/water H3 R5 cells whose centres are inside
the closed rectangle; no land cells are added. Empty selections fail. Omitted
bounds retain the full study. Subset catalogs record the rectangle and parent
membership hash alongside selected membership hashes. External validators check
that selection against the approved registry. Changing a saved region requires rebuild.

`OUTPUT_DIRECTORY` is the Weather root; solar products append their named subdirectories.
Explicit `output_dir` always names the exact destination. Output paths and their
ancestors must not be symlinks, including dangling links and companion
subdirectories. This is checked before path resolution, study loading or writes. File-relative output
and study paths resolve beside the file; argument/environment paths resolve from
the working directory. Without configured output, all three producers use the study's Data root
under `Meteorology/`. The toolkit checkout is never a data destination. Personal `.env` files are ignored and
excluded from packages; only `.env.example` is committed.

`REBUILD=false` validates and preserves existing rows and nulls, extending the
continuous union of saved and requested dates, including intervening gaps.
Covered requests are no-ops. Weather also backfills new metrics across saved dates.
`REBUILD=true` replaces the selected product with the requested range after
staged validation, preserving companions. Solar products retain the existing
producer-code identity guard: different producer code requires explicit rebuild.

Credentials resolve from process environment, selected file, then `CDSAPI_RC`
or `~/.cdsapirc`. Prefer `CDSAPI_KEY`; legacy `COPERNICUS_API_KEY` is
accepted. Empty tokens allow fallback. `CDSAPI_URL` must identify the supported
official endpoint. Tokens never enter preflight output, run settings or catalogs.
Solar Cycle needs no credentials. Importing the package performs no acquisition.

```sh
meteorology get --cache-dir /path/to/retained-cache
meteorology get 2009-01-01 2009-01-02 --output-dir scratch/weather --no-verbose
```

```python
get_weather()  # dates/output/verbosity/metrics/rebuild from local settings
get_weather(env_file="/path/to/.env", verbosity=False)
```

Verbose progress uses timestamped UTC lines on stderr. It shows the resolved nonsecret
settings (after argument/environment/file precedence), selected settings-file path,
mode, saved-product validation time, requested and final output ranges/metrics,
missing-date intervals and new metric backfill. Query plans identify native fields.
Source messages identify the official endpoint, verified object/array cache reuse,
and each actual API call with request number, attempt 1/1, elapsed seconds and bytes.

There are currently no automatic API retries. Startup and API messages state this
policy; a failed call reports elapsed time and exception type with details redacted.
A manual rerun reuses verified completed objects. Messages never claim a retry or
network request for cache reuse and never print credentials, authorization headers,
response bodies or raw exception text.

Each day reports query/cache time separately from processing/write time. The final
summary includes source metadata/cache time, reduction/retained-row merge/write
time, checksum/evidence finalization, publication, total elapsed time and actual
request/byte counts. Query/cache time can include disk reads and chunk decoding;
per-API timings isolate retrieval. Total elapsed time also includes other overhead.
Contained requests explicitly report no missing work and zero query/processing/API
calls after validation. `VERBOSITY=False` silences these progress messages. CLI
stdout retains only the resulting catalog path. These
settings do not enable the incomplete-final-day option: explicitly pass
`--allow-incomplete-final-day` / `allow_incomplete_final_day=True` when qualifying
that boundary. They do not select historical source epochs.
A fresh source vintage may revise values; use scratch outputs for verification.

## Preflight

`meteorology preflight` (or `meteorology preflight --env-file PATH`) validates run
settings only and prints the resolved nonsecret values. It performs no study/credential
loading, directory creation, locking, source access, or publication. The same
`meteorology.run_settings.preflight` function runs automatically before `get`
starts work. Its checks cover:

- Required Weather dates present; optional settings use validated defaults.
- Four bounds supplied together, finite, ordered and within WGS84 limits.
- Valid ISO calendar dates with START_DATE <= END_DATE.
- A nonempty, unique list of allowed metric families.
- True/False verbosity and rebuild flags.
- A usable OUTPUT_DIRECTORY, with an existing directory ancestor that is writable/searchable.
- Readable UTF-8 settings, well-formed assignments and no repeated known keys.

Errors are collected into one message naming keys and line numbers, without
printing raw values or unrelated credentials. Overrides retain the documented
precedence. The preflight is a local configuration check, not
proof of future source availability, credentials, free-space sufficiency or
scientific quality. Study gates, existing-product validation, actual source cutoff,
and publication-time storage checks still run at their corresponding stages.
Filesystem access is checked without probing writes and can change after preflight.

```sh
meteorology preflight
```

## Source and working-data lifecycle

The supported `get_weather` API stores downloads, processing candidates, journals
and stable locks in `output_dir/tmp/weather-<destination-hash>`.
Solar companions use `output_dir.parent/tmp/<product>-<destination-hash>`.
Thus configured production runs share `Data/Meteorology/tmp/`.
The toolkit checkout rejects data destinations. The `tmp/` directory is created
on demand; no empty directory needs to be committed. Existing work in the former
`.marinecast-work/meteorology` location is not moved or deleted automatically.
Finish or recover an older interrupted run with its original version before
switching storage layouts; do not run old and new versions against one destination.
Normal successful runs remove owned payloads and staging, retaining only small
lock, ownership and recovery records. Unrelated files under `tmp/` are preserved.
Default downloads use an owned child of this work area. An explicit `cache_dir`
selects a work base with a destination-specific owned child, never permission to
clear unrelated files. The lower-level compatibility `get_data` preserves an
explicit caller cache; it is not the supported root product API.

After publication, the complete final files/catalog are reopened and validated
before owned source objects, processed arrays, mapping caches and temporary evidence
are removed. Receipts/hashes are embedded in `catalog.json`; timestamps and source
epochs remain explicit. Failed runs retain completed inputs for manual resumption.
Successful contained requests need no source archive. Future acquisition or a full
rebuild may download data again and can encounter revised provider values. This
policy preserves external study grids/masks and caller-owned sources.

### Interrupted runs and cleanup failures

Each new publication records a small owner marker outside its staging directory
before writing candidates. Under the exclusive output lock, the next run resolves
transaction journals first and then removes only staging with a matching marker.
Unmarked legacy staging and unrelated files remain untouched; this is not an age
sweep or authorization to clean historical caches. Source payloads from failed
acquisition remain resumable independently of discarded output candidates.

Transaction cleanup retains the commit/rollback journal until backups are removed.
A deletion failure reports incomplete cleanup and, for a committed transaction,
explicitly says publication committed. It does not claim a successful cleanup or
roll back a committed product. Staging cleanup failures preserve their ownership
marker for recovery. When work already failed, the original exception retains a
cleanup note instead of being hidden. Validated Weather and Radiation no-op runs
can retry source cleanup without opening the provider. Small lock/ownership files
and empty control directories may remain under `tmp/`.

## Files and quantities

- `meteorology-hourly.parquet`: H3_INDEX string, DATETIME timestamp(us, UTC), selected float64 metrics.
- `meteorology-daily.parquet`: H3_INDEX string, DATE date32, selected float64 metrics.
- `catalog.json`: method, source metadata/evidence identities, units, dates, hashes and value-status totals.

The separate [Solar Cycle](solar-cycle.md) has its own child directory and catalog.
Weather accepts the recognized complete `solar-cycle/` and
[Solar Radiation](solar-radiation.md) `solar-radiation/` companions alongside its
three files and the regular `tmp/` directory; other unrelated entries still reject. Neither adds Weather families.

All families yield 10 hourly and 14 daily metric columns. The returned result holds
hourly_path, daily_path and catalog_path. Unrelated output-directory files cause
an error in either mode.

## Rebuild and append modes

`METRICS` is a comma-separated list of the allowed family names above; the example
file comments list them. Explicit `variable_list` / `--variables` overrides it.
`rebuild=True` / `--rebuild` / `REBUILD=True` replaces existing weather files with
exactly the requested dates and metrics. `rebuild=False` / `--no-rebuild` /
`REBUILD=False` (the default) preserves existing dates and acquires only missing
dates and new metrics across saved dates. Both modes build normally if the destination is absent or empty.

Append inspects a complete three-file product and validates hashes, schemas,
units, UTC keys, membership and status/null totals before acquisition. Requests
with all dates and selected metrics already present return unchanged paths with
zero acquisition and no publication. Otherwise the final range is the union of saved and requested
dates. Only uncovered dates and newly requested metric dependencies trigger cube queries.
For disjoint ranges, the intervening dates are also fetched to retain the complete
UTC day lattice. Invalid existing files are not guessed around: use explicit
rebuild to replace a corrupt/incomplete weather product after inspection. Unrelated
files and symlink entries remain errors in both modes.

Append requires the same approved membership/envelope. Newly requested metrics
are acquired across the entire saved date range, including dates outside a narrower
new request. Old metric columns remain present even if omitted from the new list;
new dates include their values too. Saved columns are never reacquired or replaced
except when their native fields are dependencies of a new derived metric. Any such
retrieved dependencies are used for the new metric only; saved values remain
unchanged. Added columns align by exact cell/time keys. Metric-scoped source epochs
record that new metrics may come from a different source vintage. Existing valid
nulls, including a retained partial rainfall day,
are not missing dates and are preserved. Revising finite values requires an explicit rebuild. The narrowly scoped
frontier maintenance operation below can fill a documented missing terminal
precipitation interval while preserving every retained finite value. The incomplete-final-day option still governs newly acquired
terminal support; it is not enabled by either rebuild setting.

Old values and null/status totals are retained; `source_epochs` preserves their
vintages separately from new intervals. No single source snapshot hash describes
a combined product. Because the deliverable is two monolithic Parquets, append
streams old row groups and new days into staged replacement files, requiring
space for the combined output; it does not redownload saved days or load the full
history into memory. A shared output lock covers inspection through publication,
and interrupted publication uses the existing recovery journal. Source-cache
snapshots stay pinned: a stale frontier still requires an explicitly selected
fresh cache to qualify newer source coverage.

```sh
meteorology get --no-rebuild
meteorology get 2009-01-01 2009-01-02 --rebuild --output-dir scratch/weather
```


| Family | Hourly values | Daily reductions |
| --- | --- | --- |
| temperature | 2 m temperature, °C | mean, minimum, maximum |
| dewpoint | 2 m dewpoint, °C | mean |
| relative_humidity | derived 2 m RH, % | mean with all 24 supported hours |
| wind | 10 m U/V and speed, m/s; FROM direction, degrees | U/V means, speed mean/max, direction of mean vector |
| cloud_cover | total cloud cover, % | mean |
| pressure | mean sea-level pressure, hPa | mean, minimum |
| precipitation | following one-hour accumulation, mm | sum of 24 intervals |

Temperature/dewpoint convert Kelvin to Celsius, pressure Pa to hPa, cloud fraction
to percent, rainfall metres to mm. RH uses the Bolton-water derivation and retains
only finite 0–100.01% values with dewpoint at least −123.15°C; unsupported values
stay null. Calm wind direction is undefined at speed ≤1e-12 m/s. Daily direction
uses mean U/V; speed uses hourly magnitude. Min/max are extrema of hourly samples,
not within-hour extremes or gusts. Nearest quarter-degree source sampling adds no
independent H3-scale weather information (effective resolution approximately 31 km).

Hourly DATETIME is UTC hour start. Instantaneous fields use that timestamp;
rainfall is the interval following it, read at the interval-end timestamp.
Daily rainfall therefore uses 01:00 through next midnight; other fields use
00:00–23:00. Daily values require complete support. Nulls are never filled with zero.

## Source, cache and cutoff

Source: ECMWF geographically chunked ERA5 single-level Zarr, attributed to
Copernicus Climate Change Service / ECMWF, CC BY 4.0. See the
[ECMWF ARCO guide](https://confluence.ecmwf.int/pages/viewpage.action?pageId=505390919).
Credentials resolve from CDSAPI_KEY, CDSAPI_RC or ~/.cdsapirc. Tokens go only to the
fixed official HTTPS endpoint; redirects and proxy forwarding are disabled.
A complete retained-cache replay requires no token or provider request.

Without run settings: output_dir=study Data root / "Meteorology", private tmp workspace,
workers=4 (allowed 1–4), transfer_bytes=8 GiB, max_seconds=3600 (up to 86400).
The Python API accepts these keyword options. Objects/arrays are checksummed;
metadata pins one source vintage. Rerun a failed call to reuse completed objects.
No automatic transient retry or eviction policy is implemented. A fresh cache
intentionally discovers a newer vintage, which can revise historical values.
Source payloads must remain separate from final product files. Publication is recoverable across its three
files; readers should verify catalog hashes and retry during replacement.

Dates beyond cached source support fail. Default rainfall needs next midnight.
`allow_incomplete_final_day=True` permits only the missing final boundary hour:
the retained October 10, 2026 source has 23 rainfall hours, null daily rainfall and
null hourly rainfall at 23:00. October 9 is the last complete source day for all
fields. Instantaneous-only requests do not need next midnight. Dates are not
silently shortened. ERA5/ERA5T remain retrospective; revision and historical
operational availability are unknown, and no forecast qualification is implied.

## Checks and CLI

```sh
meteorology get 2009-01-01 2009-01-02 --variables temperature wind --study-config /path/to/config/study.v1.json --output-dir scratch/weather --cache-dir /path/to/source-cache --no-verbose --no-rebuild
meteorology check scratch/weather/catalog.json --study-config /path/to/config/study.v1.json
```

`python -m meteorology` and `python -m meteorology.regional` expose the same commands.
`get` also accepts --allow-incomplete-final-day, --env-file, --verbose/--no-verbose, and --rebuild/--no-rebuild;
it has no dry-run option.
The Python API exposes additional resource caps. `check_weather` streams one day
at a time to verify hashes, schema/types/units, exact UTC lattice and uniqueness,
member consistency, finite/null representation and status totals. Pass study_config
to revalidate the external registry/mask; otherwise membership_verified is false.
`validate_product` remains a small alias for this weather contract. These checks
are structural/source consistency checks, not independent observational truth.

::: meteorology.regional.get_weather
    options:
      show_source: false

::: meteorology.regional.check_weather
    options:
      show_source: false

## Explicit frontier repair and refresh

`meteorology.refresh.refresh_weather` is an explicit maintenance operation for the
complete 14-daily/10-hourly approved product. It is separate from ordinary append,
which continues to preserve existing nulls. Inspect fresh source coordinates first
and choose a supported `end_date`; the function never assumes a calendar lag.

```python
from meteorology.refresh import refresh_weather
refresh_weather("2026-10-20", study_config="/path/to/study.v1.json",
    output_dir="/path/to/Data/Meteorology",
    cache_dir="/path/to/retained/frontier-20261026", workers=1,
    allow_incomplete_final_day=True)
```

The operation holds the publication lock, validates the current product, and
accepts only one explicitly documented missing terminal precipitation interval.
It preserves the retained 23 hourly rainfall values, replaces only the missing
23:00 UTC hourly value using the following midnight's interval-ending source,
and sums those 24 hours for that cell's daily total. Fresh source revisions to
previously finite values are ignored. Missing replacement support fails before
publication. New days use all seven source fields and the normal UTC reductions.

`endpoint_repairs` records the interval, superseded catalog hash, new source
metadata/evidence hashes, support cutoff and null-only replacement policy. These
records qualify the otherwise retained `source_epochs`: only the specified
missing interval and its daily sum are replaced, and the other 23 intervals retain
their original epoch. New date epochs and old source receipts remain explicit.
ERA5 versus ERA5T identity and historical operational availability remain unknown.

Every old row is compared with the staged product before promotion, exempting only
the authorized repaired nulls. Structural checks run before and after publication.
A fully covered, repaired request is a validated no-op. A repeated request at
the same pinned partial frontier also leaves the files unchanged when the missing
next-midnight interval remains unavailable. Failed acquisition retains
verified cache objects for manual restart; there are no automatic retries. The
`cache_dir` is now a work base with an owned `refresh-<destination-hash>` child.
Successful final readback removes its downloaded objects, decoded arrays, mapping
and temporary evidence. A successful coverage-only probe also removes its payloads.
Failed acquisition keeps verified objects for resumption; a different work base
can discover a newer provider vintage. Existing caches made by earlier refresh
versions are caller-owned: they are neither adopted nor swept.

### Validated October 26, 2026 refresh

The current coastal product covers 2009-01-01 through 2026-10-20: 463 cells,
3,010,426 daily rows (14 metrics), and 72,250,224 hourly rows (10 metrics).
October 10's 463 missing terminal hourly rainfall values and daily totals were
repaired without revising retained finite values. October 19 is the newest day
with complete temporal support. October 20 retains 463 unavailable final hourly
rainfall values and 463 partial daily totals because October 21 midnight is
absent from this source snapshot. Unsupported RH remains null.

The bounded refresh downloaded 441 field chunks (269,095,962 bytes), plus a small
metadata/coordinate probe, using one worker. Sources and receipts are retained.
All unaffected historical rows compared exactly before promotion; staged and
final product checks, source-boundary and daily/hourly accumulation checks, and
an unchanged same-frontier rerun passed. See the [dated evidence](evidence/frontier-refresh-20261026.json).
This confirms structural/source consistency, not observational accuracy, ERA5
revision identity, historical operational availability, or forecast readiness.


## Source lineage validation

As of 2026-11-03, `check_weather` verifies the SHA256 of inline source evidence
using the producer's sorted-key JSON serialization, and binds each epoch's metadata
identity to that evidence. Single-vintage catalogs use their top-level hashes;
combined catalogs must have nonempty source epochs. Every delivered hourly/daily
metric must have exactly one epoch covering each product date, without gaps,
overlaps, duplicate metric names or out-of-range dates. Metric backfills may overlap
in time only when their metric scopes are disjoint. Legacy epochs without a metric
scope apply to all delivered metrics.

Endpoint repair records additionally require matching evidence, an in-range date,
precipitation metric, next-midnight interval end and the reporting-cell count.
They supplement the original vintage rather than erase it. Historical catalog
byte hashes remain receipts; reserializing embedded JSON is not a substitute for
those original bytes. The checker does not open retained-directory paths or contact
providers. Legacy catalogs missing inline evidence are not certified by this check;
recover their actual evidence through an explicitly scoped migration rather than
inventing receipts. The retained merger carries inline evidence forward and checks lineage before
promotion; source products without that evidence require explicit reconciliation.

These checks run before covered-date no-ops and append acquisition, and during
staged/final readback. They validate internal lineage consistency, not provider
truth, historical operational availability or independent meteorological accuracy.

Weather initialization and explicit refresh recognize the complete named
`solar-cycle` and `solar-radiation` companions. Either companion may be built first;
an otherwise absent Weather product is created in normal append mode. Unrelated
entries, symlinked children and incomplete Weather products remain rejected.
