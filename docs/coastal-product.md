# Current coastal ERA5 history

`MarineCast/Data/Meteorology` is the current approved coastal R5 product. It contains
one hourly Parquet, one daily Parquet and one catalog. The approved shared-study
configuration supplies exact water-reporting membership; the acquisition rectangle
supplies atmospheric support only. Nearest quarter-degree source sampling does not
create independent R5 weather detail. Native compressed objects, checksummed
regional arrays, source evidence and mapping remain in the private ERA5 caches.

The current run reuses the verified 2009-01-01–2026-10-08 rectangle product and
adds 2026-10-09–2026-10-10 from the current ECMWF geographically chunked store. It preserves
the earlier source vintage and records the new vintage as a separate source epoch.
Retrospective revisions are not silently substituted. Final ERA5 versus ERA5T,
forecast init/lead and historical publication times are unavailable in this
representation. Retrospective consistency does not establish operational forecast
availability or independent observed accuracy.

The 14 daily metrics and scientific arithmetic remain those of the
[regional API](regional-api.md). Instantaneous inputs are hours 00–23 UTC;
precipitation comprises the one-hour intervals ending 01 through next midnight.
Unsupported RH stays null. October 10 has 24 supported instantaneous hours, but its
precipitation has only 23 of 24 required intervals and remains null; the last
complete all-metric day is October 9. No metric is filled with zero or clipped. Visibility,
fog, radiation, gusts and snowfall are not supplied by this seven-field product;
they must not be inferred from its cloud, precipitation or mean wind fields.

## Reproduce the retained-data publication

From the installed toolkit environment, run the following with absolute paths
(or from the owning checkout with `PYTHONPATH=src`). This command verifies source
checksums, date adjacency, exact daily membership, unique date/time/cell keys,
complete hourly support and schemas before transactional publication. A failed
publication leaves the prior current product recoverable. Rerun the same command
to rebuild from the retained sources; it performs no network requests.

```sh
python -m meteorology.coastal \
  --study-config /Users/tylerstevenson/Documents/Code_Repos/MarineCast/config/study.v1.json \
  --source /Users/tylerstevenson/Documents/Code_Repos/MarineCast/Data/.meteorology-update/retained-rectangle-20261015 \
  --source /Users/tylerstevenson/Documents/Code_Repos/MarineCast/Data/.meteorology-update/tail \
  --output-dir /Users/tylerstevenson/Documents/Code_Repos/MarineCast/Data/Meteorology \
  --control-dir /Users/tylerstevenson/Documents/Code_Repos/MarineCast/Data/.meteorology-coastal-publication
```

The prior rectangle files are retained with hard links in the private update area.
They preserve the preexisting product without an extra full-file copy. Do not
remove that source or its caches until a separately approved retention change.
The small tail product is a retained source partition, not a second current release.

## Update source coverage

Discover the official store's current time-coordinate frontier using `Archive`
with a fresh private cache. Its final timestamp must include the next midnight;
therefore an end at October 10 23:00 qualifies October 9 as the last complete day.
With `allow_incomplete_final_day=True`, retain the final fully sampled instantaneous
day while leaving its unsupported precipitation null. This opt-in accepts exactly
one missing terminal hour; larger gaps still reject publication. Keep the frontier
and all seven source shapes in the run evidence. Never use a
calendar date or expected lag as proof of actual source availability.

For the next update use the existing authorized credential resolver and call
`meteorology.get_data('2026-10-09', latest_instantaneous_day, bbox=approved_bbox,
output_dir=tail_dir, cache_dir=fresh_cache, workers=2,
transfer_bytes=4 * 1024**3, max_seconds=3600,
allow_incomplete_final_day=True)`. The existing tail is transactionally
replaced only after success. Re-run the publication command above. Pinning a fresh
cache intentionally selects the new source epoch only for the tail; the historical
rectangle remains unchanged. Existing credentials can be loaded with
`resolve_credentials(env_file=workspace / '.env', required=True)` and assigned in
process to `CDSAPI_KEY`; never print or persist the token. This neither creates
credentials nor accepts terms. A failed bounded call resumes from its verified
objects when repeated with the same cache; a newer vintage needs a fresh cache.

Check available storage before each run: the first new geographic time block
requires hundreds of MiB of source/archive storage, and staging the coastal hourly
and daily products requires roughly 3 GiB plus headroom. Limits remain bounded;
no preexisting files are deleted and no HRRR historical collection is launched.

## Validate

Run `python -m pytest -q tests/test_coastal.py tests/test_regional.py
 tests/test_marine_reporting.py`, then the full suite. Read back both current
Parquets and verify catalog checksums, date/time ranges, exact registry membership,
row uniqueness/counts and null counts. Compare boundary/leap-day/first/last-day
values with retained source products and independently recompute sampled daily
aggregates from hourly inputs. Requested future/unavailable dates remain in the
catalog's unavailable interval. Source and method lineage are in `source_epochs`
and `study_identity`; current registry mask and membership hashes are explicit.

An executable bounded update helper is retained at
`MarineCast/Data/.meteorology-update/update_current.py`. From this checkout:

```sh
# Metadata-only frontier/plan; use a fresh cache for a newer source vintage.
PYTHONPATH=src .venv/bin/python ../../Data/.meteorology-update/update_current.py \
  --cache-dir ../../Data/.meteorology-cache-YYYYMMDD
# Resume the same bounded acquisition and publish exact coastal membership.
PYTHONPATH=src .venv/bin/python ../../Data/.meteorology-update/update_current.py \
  --cache-dir ../../Data/.meteorology-cache-YYYYMMDD --run
# Full independent streaming readback plus sampled native arithmetic checks.
PYTHONPATH=src .venv/bin/python ../../Data/.meteorology-update/validate_current.py
```

The independent validator is the retained October 2026 acceptance check, with
explicit expected membership/null counts and sampled dates. Update its expected
frontier/null assertions when qualifying a later vintage; do not interpret an
old acceptance report as validation of a new update.

`Data/.meteorology-update/native-sampling-crosswalk.parquet` records each approved
cell's source-grid index, latitude/longitude, distance, grid hash and method. The
463 reporting cells sample 243 distinct native points; the largest recorded
centroid-to-source distance is about 16.4 km. This is a sampling witness, not an
independent weather measurement or a water-only atmospheric average.

The refreshed provider chunks span 2024-10-22–2026-10-10 because the geographic
store compresses a long time series into each spatial object. Retrieving the new
dates therefore transfers overlapping older hours in those changed tail chunks.
The 6,490 retained historical product days remain unchanged; their source vintage
is preserved rather than replaced by the refreshed overlap. Earlier time blocks
were reused, and no parallel historical HRRR acquisition was performed.

The [official ARCO guide](https://confluence.ecmwf.int/spaces/CKB/pages/505390919/ERA5+hourly+Analysis+Ready+Cloud+Optimised+ARCO+data+on+single+levels+from+1940+to+present+Product+User+Guide+PUG)
identifies accumulation time as validity time and documents the dropped forecast
step metadata. [ERA5's accumulation documentation](https://confluence.ecmwf.int/spaces/CKB/pages/76414402/ERA5+data+documentation)
states that reanalysis accumulations cover the preceding hour and that midnight
accumulations belong to the preceding day. The local source metadata records
`tp` as accumulated total precipitation in metres. This supports the unchanged
01:00-through-next-00:00 daily precipitation convention and explicit missing
terminal interval; no forecast initialization or lead is reconstructed.

Run one updater at a time and keep the declared retained inputs immutable until
publication completes. Daily values summarize the full UTC day; they cannot be
treated as inputs available at that day's midnight or at an earlier forecast
issue time. A forecast application needs an issue-time-qualified forecast archive
or an independently justified lag/as-of policy. This product does not establish
that policy or backfill missing historical availability timestamps.

The [2026-10-16 acceptance evidence](evidence/coastal-era5-20261016.json) records
both product checksums, complete readback statistics, seven-date native checks,
code hashes, source epochs and the final live frontier confirmation at 22:24 UTC.
The current product passed 544 offline tests (one opt-in NOAA test skipped),
42 clean installed-wheel checks without optional visualization packages, and
independent validation of every daily/hourly key, membership and null count.


## Primary API verification, 2026-10-17

The installed `get_weather(start_date, end_date, variable_list)` now produces the
approved coastal membership directly from the fast regional source, without a
rectangle export. See the [primary API](regional-api.md#approved-coastal-api) and
[quickstart](getting-started/quickstart.md). The retained merger above remains a
compatibility route for the original two-epoch publication.

Verification on Python 3.12.13 used existing environments and retained source caches:

| Check | Result |
| --- | --- |
| Full offline suite | 554 passed; one opt-in NOAA test skipped; two expected invalid-RH fixture warnings |
| Installed wheel, outside checkout | 52 offline checks passed |
| Installed-wheel retained-source replay | 2009-01-01 and 2026-10-09–10; every key/value exactly matched current files for all 463 cells and all 14 daily / 10 hourly fields; zero provider requests |
| Full current structural scan | 3,005,796 daily and 72,139,104 hourly rows passed hashes, schema/types/units, exact UTC keys, uniqueness, membership and null/status totals |
| Lint, strict docs, wheel/sdist builds | Passed |

Approved Data files were unchanged. This is bounded numerical replay plus a full
structural scan, not a full historical reconstruction or fresh-source qualification.
No live provider acquisition, independent observational validation or Python
3.11/3.13 environment run was performed. Twine was unavailable in existing
environments; distribution builds and installed-wheel execution passed.

Remaining work includes a portable multi-epoch replay descriptor, stricter source
contract/time gates inside the legacy retained merger, bounded automatic retries,
and separate legacy-route/dependency retirement. These are not represented as fixed
by the primary API. The current source metadata/evidence snapshots remain pinned;
a fresh ERA5/ERA5T vintage may revise historical values.
