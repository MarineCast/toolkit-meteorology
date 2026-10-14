# Remaining request API review findings: offline evidence

The post-merge lifecycle patch is recorded in
[the lifecycle section below](#post-merge-lifecycle-reliability-2026-10-14).
Earlier sections retain the original PR 12 evidence and its validation dates.

Inspected and fetched branch: `codex/meteorology-request-api`, PR 12, at
`8a7e42d85bff4982cd91d384287a6da2e6ae2329`; local worktree clean and upstream
identical. All four findings still existed. Only this owner repository changed.
No live acquisition, backfill, dataset rewrite, merge or release was performed.

## Reproduction → fix → regression → observed result

| Finding at reviewed head | Reproduction | Fix | Regression and observed result |
| --- | --- | --- | --- |
| Revision selection existed | Temperature-only cache followed by revised temperature/cloud refresh returned old 10 C rather than refreshed 20 C. The baseline test failed. Equal-size ordering had no declared policy. | Atomic parameter/date generation index with immutable snapshots; select vintage before representation, preserve pins and require explicit resolution for mixed coverage. | `test_request_review.py`: changed-value narrow/superset and equal-size revisions, dates, pins, partial/failure, concurrent lookup, reaggregation, corruption/recovery and failed index promotion pass. |
| Lineage defect existed | One preliminary hour marked all 96 point/hour temperature rows preliminary; preliminary cloud contaminated final temperature. Three initial lineage regressions failed. | Preserve parameter/point/time/revision identity, construct dependencies per metric/slot, then aggregate daily. v2 schema adds dependency and value-status JSON evidence. | `test_request_lineage.py`: only 4 affected point/hour rows are preliminary; daily status retains the contributor. Mixed wind/RH dependencies, distinct timestamps/receipts, missing/bitmap values, next-midnight boundaries, export and subset evidence pass. v1 pins retain historical identity. |
| Preflight defect existed | Hourly source bytes retained, daily result absent, cap 1: baseline rejected against nominal cold work. | Verify exact source objects before admission, separate cold estimates from remaining lower bounds/reservations, reserve every actual call. | `test_request_preflight.py`: warm ERA5 daily processing cap 1 makes zero calls without credentials/client; partial HRRR wind acquires only 48 missing U/V ranges. Corrupt bytes, dry runs, exact ERA5 selection and interrupted counters pass. |
| Storage defect existed | Baseline replay with 2 MiB retained history and 1 MiB staging rejected `Owned staging byte cap exceeded`. Chunk checks traversed the full namespace repeatedly. | Request-only startup reconciliation, atomic incremental write reservations/byte counters, persistent optional retention quota, independent physical-disk margin. Completed paths leave the hot ledger. | `test_request_storage.py`: the same historical-cache request succeeds. Run/retention/disk limits, partial resumption, concurrency and transitions pass. Public point-block and multi-chunk transfer tests observe one startup traversal. Legacy Budget semantics remain unchanged. |

## Commits

- `37dd72f`: committed source-vintage selection and result pins.
- `ed3b431`: hourly/metric dependency lineage and v2 contract.
- `9aa0d10`: source-aware preflight and cache-only HRRR receipt reader.
- `8b6ab8f`: request storage ledger, separate quota/disk safeguards, integration regressions and final vintage edge cases.
- `722c723`: defer HRRR HTTP transport construction until source work is missing; forbid construction in the warm-cache regression.
- `e5a10a4`: first complete review-evidence and documentation handoff.

The storage integration commit includes fixes found while exercising the four paths
together and formatting of the touched request modules. Family methods, local-day
and shared-study publication contracts retain their existing semantics.

## Baseline and validation commands

Repository `.venv`, macOS/Python 3.12, ecCodes 2.47.1. Optional codec is installed;
no generated-GRIB test is described as passed after a codec skip.

```sh
python -m pip install -e '.[dev,native-pilot]' --no-deps --no-build-isolation
python -m pytest -q tests/test_request_api.py tests/test_request_transport.py tests/test_processing_scaling.py tests/test_concurrency.py tests/test_era5_resources.py
python -m pytest -q tests/test_request_review.py tests/test_request_lineage.py tests/test_request_preflight.py tests/test_request_storage.py
python -m pytest -q
ruff check src tests benchmarks demo
meteorology catalog --check
meteorology feature-policy --check
python -m meteorology.variables --markdown > /tmp/meteorology-review-variables.md
diff -u docs/reference/variables.md /tmp/meteorology-review-variables.md
mkdocs build --strict
python -m build --no-isolation
python -m twine check dist/*
python -m pip check
git diff --check
```

Relevant pre-edit baseline: **48 passed in 23.18 s**. Initial three cache/preflight
reproductions: **2 failed, 1 passed in 4.87 s**; initial lineage reproductions:
**3 failed in 5.75 s**. Intermediate whole suites passed **463 + 1 live skip** in
208.51 s and **464 + 1 live skip** in 227.67 s as regressions were added.
Focused integration review regressions: **32 passed in 26.28 s**. Owner integration suite:
**465 passed, 1 skipped in 262.43 s** at implementation commit `8b6ab8f`.
Final owner suite at `722c723`: **466 passed, 1 skipped in 231.82 s**.
The late HRRR cache-only transport regression also passed with transport
construction forbidden; focused preflight/transport checks passed **10 tests in
18.93 s**. The skip
is the opt-in live NOAA test; generated-GRIB tests ran. This includes the existing
legacy, ERA5 fixed-pilot, native HRRR, resource, concurrency and scaling regressions.

Fresh regular wheel, installed under `/tmp` and imported from its own site-packages,
passed **67 request API/transport/review regressions in 47.83 s**, using Python 3.12,
ecCodes 2.49, pandas 3.0.6, Arrow 26 and no Folium/Branca/GeoPandas. Core
source/variable/get planning, legacy synthetic weather/daylight/lunar/matrix
generation, 514-row matrix validation and `pip check` passed outside the checkout.
Codec/test extras were added only after the core-only checks.
Ruff, catalog/policy parity, generated variable-doc parity, wheel/sdist build,
Twine, development/installed `pip check` and strict MkDocs passed.
The final rendered site has **39 HTML pages and 2,216 verified internal links/anchors**.
Build uses installed build dependencies without isolation; the first editable
install with isolation was blocked by sandbox DNS, then the no-isolation install
passed. First strict-doc attempt caught a not-yet-written evidence-page link;
the completed documentation is rebuilt strictly.

## Before/after workload

`benchmarks/request_review.py` runs generated tiny HRRR messages and mocked HTTP,
then 100 storage checks with 0/100/1000 tiny retained files. The before replay uses
read-only `git archive 8a7e42d src` through `PYTHONPATH`; after uses implementation commit `722c723`.
Before equivalent processing uses cap 1024; after uses cap 1 for warm source and
48 for partial source, below nominal cold counts. A separate original-head cap
1/48 replay rejects both paths. Inputs and requested quantities are otherwise
equivalent. The JSON snapshots contain the actual measurements:
[before](evidence/request-review-before.json), [after](evidence/request-review-after.json).
No timing threshold is imposed; tests assert operations and correctness.

| Workload | Requests before/after | Bytes before/after | Decodes before/after | Traversals before/after | Stat calls before/after | Wall seconds before/after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| warm-final | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 102 / 103 | 0.009677 / 0.008692 |
| warm-source | 0 / 0 | 0 / 0 | 24 / 24 | 2 / 1 | 1168 / 1252 | 0.461431 / 0.426314 |
| partial-source | 48 / 48 | 9024 / 9024 | 24 / 24 | 148 / 1 | 74214 / 4937 | 2.663415 / 1.773790 |

No intermediate normalized Parquet is read in these HRRR workloads: row-group reads
are 0 before/after. The ERA5 regression still exercises point-block normalization
and daily reaggregation; exact retained-source reuse makes zero provider calls.

| Workload | Current RSS bytes before/after | Historical peak bytes before/after | Namespace bytes before/after | Run-tree control bytes before/after | Unfinished partial bytes before/after |
| --- | ---: | ---: | ---: | ---: | ---: |
| warm-final | 287080448 / 284180480 | 287080448 / 284180480 | 112172 / 108114 | 604 / 894 | 0 / 0 |
| warm-source | 289046528 / 286998528 | 289046528 / 286998528 | 182644 / 178613 | 1200 / 1775 | 0 / 0 |
| partial-source | 291176448 / 289669120 | 291176448 / 289669120 | 347699 / 399804 | 1802 / 2664 | 0 / 0 |

The namespace total includes immutable results, raw sources, generation snapshots,
retained run journals and accounting controls. Successful runs have no active
partial payloads; run-tree bytes are retained controls, not temporary acquisition.
Ledger admission additionally reserves a 64 KiB run control allowance and planned
write sizes. macOS `ru_maxrss` is reported in bytes. RSS is descriptive, not a
native allocation guarantee; runs shared the local development environment.

| Retained tiny files | Traversals before/after | Stat calls before/after | Wall seconds before/after |
| --- | ---: | ---: | ---: |
| 0 | 100 / 1 | 301 / 209 | 0.024436 / 0.075276 |
| 100 | 100 / 1 | 20301 / 409 | 0.261485 / 0.087080 |
| 1000 | 100 / 1 | 200301 / 2209 | 2.419398 / 0.125990 |

The measured count reduction does not establish a general wall-time speedup.
Durable leases, atomic ledger writes, active-evidence validation and richer lineage
add CPU/fsync/storage overhead. Source/field decode counts are unchanged; provider
bytes are unchanged in equivalent workloads. The new behavior permits bounded
reuse that the old nominal-cap preflight rejected. Startup reconciliation remains
O(namespace files), once per run/resume; active writes update counters and retain
no completed-path inventory in the hot ledger. There is no eviction policy.

## Compatibility, working examples and unrun gates

[Request API](request-api.md) provides runnable refresh, `result_id` pinning and
source-cache-only daily examples, plus CLI `--result-id` and
`--retained-cache-mib`. Overlapping refreshes update only their parameter/date
coverage. Mixed generations or unavailable exact selections inside an active
refreshed vintage require full-coverage refresh or a coherent pin. Corrupt active
results fail; healthy explicit refresh preserves old corrupt evidence. Atomic
promotion follows validated artifacts and all transfer/control accounting.
v1 artifacts are unchanged and available through explicit pins; their old lineage
is not certified as v2. ERA5 exact selection/source identity is preserved.

Retention defaults unbounded with a 16 MiB physical-disk safeguard; an optional
persistent quota counts the namespace and reservations. Source writes under shared
paths are charged during staging. Interrupted allocations stay reserved; failed
attempts remain inspectable. Legacy fixed pilots retain aggregate staging semantics.
No new provider, geographic support, regional accuracy or backfill readiness claim.

Small opt-in qualification commands, **not run**:

```sh
METEOROLOGY_LIVE_HRRR=1 python -m pytest -q tests/test_live_hrrr.py
python -m meteorology.era5.pilot plan --study-config /path/study.v1.json > /path/reviewed-era5-plan.json
# After reviewing that exact fixed one-day plan, explicitly opt into acquisition:
python -m meteorology.era5.pilot run --study-config /path/study.v1.json \
  --plan-json /path/reviewed-era5-plan.json --output /path/new-private-era5-pilot
python -m meteorology.hourly_weather.native_day plan --study-config /path/study.v1.json > /path/reviewed-hrrr-plan.json
python -m meteorology.hourly_weather.native_day run --study-config /path/study.v1.json \
  --plan-json /path/reviewed-hrrr-plan.json --output /path/new-private-hrrr-pilot
```

Use the exact bounded plans and reviewed inputs in
[CDS pilot guidance](CDS_JOB_INTEGRATION_AND_PILOTS.md) and
[HRRR native-day pilot](HRRR_NATIVE_DAY_PILOT.md) before any `run`. CDS credentials,
terms, real NOAA/CDS compatibility, independent regional accuracy and production
shared-study qualification were not exercised by this offline work.

## Files changed

- `mkdocs.yml`
- `.github/workflows/tests.yml`
- `benchmarks/request_review.py`
- `docs/CONTRACTS.md`
- `docs/DEVELOPMENT.md`
- `docs/evidence/request-review-after.json`
- `docs/evidence/request-review-before.json`
- `docs/reference/api.md`
- `docs/reference/cli.md`
- `docs/request-api.md`
- `docs/request-handoff.md`
- `docs/request-review-evidence.md`
- `src/meteorology/era5/jobs.py`
- `src/meteorology/era5/resources.py`
- `src/meteorology/hourly_weather/native_day.py`
- `src/meteorology/request.py`
- `src/meteorology/request_adapters.py`
- `src/meteorology/request_cache.py`
- `src/meteorology/request_cli.py`
- `src/meteorology/request_lineage.py`
- `src/meteorology/request_sources.py`
- `src/meteorology/request_storage.py`
- `src/meteorology/request_vintage.py`
- `tests/request_fixtures.py`
- `tests/request_provider.py`
- `tests/test_request_lineage.py`
- `tests/test_request_preflight.py`
- `tests/test_request_review.py`
- `tests/test_request_storage.py`

## Hosted checks

The original PR head `8a7e42d` had observed successful Linux tests (3.11, 3.12,
3.13), wheel and macOS smoke on 2026-10-13. Those outcomes do not validate these
new commits. The existing matrix is preserved; macOS smoke now explicitly includes
the four new review regression files. Current-head hosted state is recorded at
handoff; local checks do not substitute for it. At `722c723`, wheel and macOS
smoke were observed successful; Linux tests (3.11/3.12/3.13) were still running
at that snapshot.

## Post-merge lifecycle reliability, 2026-10-14

Fetched current main was exactly `368ddfcce690d02a2e0b0cade6817b6675e90307`.
Both remaining findings existed there. Work uses the new branch
`codex/meteorology-lifecycle-reliability`; only this owner checkout changed.

| Reproduction against main | Fix | Regression and observed outcome |
| --- | --- | --- |
| Retain a 30,000-byte run file under a 100,000-byte staging cap, reopen, then reserve 5,000 bytes: staging incorrectly becomes 95,536 bytes and rejects the reservation. Public daily → refreshed hourly → daily processing also inherits the old execution's original limits. | Validated durable retained-path evidence and operational ledger v2; execution identity binds logical request and selected source generation, with unfinished refresh profiles preserving resume identity. | `test_request_lifecycle.py`: repeated reopen stays at 65,536 bytes, namespace/quota still counts retained data, genuine unfinished caps remain enforced, concurrent reaggregation returns one identity, and refreshed daily values use zero provider calls without changing old artifacts. |
| Inject failure in active-index final accounting: refresh raises after the new generation becomes active. | Writer witnesses distinguish replacement from directory-fsync durability; versioned publication intent/receipt precedes commit. Confirmed commits return a result with operational warnings; unknown durability raises an explicit reconciliation error. | `test_request_commit.py`: before-pointer, writer accounting, both post-index retention operations, persistence, receipt, fsync, secondary-write, KeyboardInterrupt/SystemExit and bounded subprocess exit/restart cases pass. Old pins remain valid; recovery is idempotent and acquisition journals remain byte-identical. |

The three initial reproductions failed **3/3 in 3.11 s** before implementation.
Implementation commits: `6567e8b` (retention/execution), `644fb7f`
(commit protocol/recovery), `3a57866` (preserve unresolved commit evidence).
The [request lifecycle contract](request-api.md#execution-lifecycle-and-recovery)
documents warnings, `WeatherResult.reconcile()`, conservative legacy handling and
fingerprint effects. Scientific method/schema v2 and immutable v1/v2 products are
unchanged. New producer fingerprints may reaggregate compatible retained sources;
explicit old result pins remain readable. No acquisition budgets are reset.

### Commands and validation

Owner Python 3.12.13/ecCodes 2.47.1; generated codecs ran. The pre-edit existing
six-file request suite passed **67 tests in 22.85 s**. Final commands:

```sh
.venv/bin/python -m pytest -q tests/test_request_storage.py tests/test_request_review.py tests/test_request_preflight.py tests/test_request_lineage.py tests/test_request_api.py tests/test_request_transport.py tests/test_request_lifecycle.py tests/test_request_commit.py tests/test_request_qualification.py
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests benchmarks demo
.venv/bin/meteorology catalog --check
.venv/bin/meteorology feature-policy --check
.venv/bin/python -m meteorology.variables --markdown > /tmp/meteorology-lifecycle-variables.md
diff -u docs/reference/variables.md /tmp/meteorology-lifecycle-variables.md
.venv/bin/mkdocs build --strict
.venv/bin/python -m build --no-isolation
.venv/bin/python -m twine check dist/*
.venv/bin/python -m pip check
.venv/bin/python benchmarks/request_lifecycle.py
git diff --check
```

Final focused suite: **94 passed in 32.20 s**. Full owner suite:
**493 passed, 1 skipped in 126.68 s**. Outside-checkout installed-wheel request
regressions: **94 passed in 32.47 s**, with ecCodes 2.49.0, pandas 3.0.6 and
Arrow 26. Ruff, catalog/policy/generated-variable parity, strict MkDocs,
wheel/sdist build, Twine, development/installed `pip check` and diff checks passed.
The only full-suite skip is opt-in live NOAA; generated GRIB and mocked transport
do not establish real-provider compatibility or independent regional accuracy.
Fresh regular wheel checks use an outside-checkout Python 3.12 virtual environment,
first core-only (no Folium/Branca/GeoPandas), then codec/test extras. Core help,
source/variable/get planning, legacy synthetic products, 514-row matrix validation
and `pip check` passed. Copied request tests use `--import-mode=importlib` against
site-packages. Linux Python 3.11/3.12/3.13 full coverage remains; macOS 3.12 smoke
explicitly includes all three new regression files. Final pushed-SHA hosted state
is reported at PR handoff, separately from these local checks.

Changed source files are `request_storage.py`, `request_cache.py`,
`request_lifecycle.py`, `request_vintage.py`, `request.py` and `era5/resources.py`.
Regression files are `test_request_lifecycle.py`, `test_request_commit.py`,
`test_request_qualification.py` and one explicit request adjustment in
`test_request_review.py`. CI/agent guidance, this evidence and JSON snapshot,
request/API/handoff docs, `benchmarks/request_lifecycle.py` and
`demo/qualify_requests.py` complete the patch.

### Bounded lifecycle sequence

`benchmarks/request_lifecycle.py` uses public ERA5 getters, generated GRIB and
mocked provider jobs. [Raw measurements](evidence/request-lifecycle.json) include
current RSS and warnings. Traversals count full-namespace `Path.rglob` walks.
Operation counts, stable reopen staging and zero repaired reservations are asserted
in the benchmark, which Linux CI runs; timing has no CI threshold.

| Step | Calls | Transfer bytes | Decodes | Traversals | Staging bytes | Namespace bytes | Reserved bytes | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Initial | 5 | 2592 | 1 | 1 | 65536 | 173783 | 0 | 0.417959 |
| Final hit | 0 | 0 | 0 | 0 | 65536 | 173783 | 0 | 0.001871 |
| Hourly refresh | 5 | 2592 | 1 | 1 | 65536 | 350944 | 0 | 0.149670 |
| Warm daily reaggregation, cap 1 | 0 | 0 | 1 | 1 | 65536 | 522425 | 0 | 0.135861 |
| Reopen | 0 | 0 | 0 | 1 | 65536 | 522425 | 0 | 0.002366 |
| Committed accounting failure | 5 | 2592 | 1 | 1 | 88812 | 700528 | 12980 | 0.140291 |
| Recovery | 0 | 0 | 0 | 1 | 65536 | 701522 | 0 | 0.012187 |

RSS ranged from 254,492,672 to 258,703,360 bytes. Namespace bytes include immutable
source/results and operational evidence; at the injected failure they also include
unfinished accounting. The 65,536-byte staging figure is the control allowance.
Recovery clears only known completed publication reservations, preserving retained
science and original transfer journals. Durable retention/commit receipts add
small JSON writes/fsyncs and storage; this correctness patch claims no general
wall-time improvement. Startup reconciliation remains one traversal per processing,
reopen or repair; ordinary final hits perform no full-namespace walk.

### Qualification and limits

The [public-getter qualification procedure](request-api.md#opt-in-qualification-through-public-getters)
has exact offline/run commands and explicit caps for HRRR temperature/vector wind,
ERA5 temperature and ERA5 precipitation/snowfall. Amounts require operator-supplied
independently vetted nonzero reference bytes, SHA and matching UTC date/bbox.
`test_request_qualification.py` exercises default zero-network planning and all
three run paths using generated fixtures only, including next-midnight intervals.
No historical wet/snowy observation is invented or silently widened.
Live NOAA/CDS, credentials/accepted terms, native/regional rebuilds and independent
scientific comparisons remain **NOT_RUN**. No merge, release or dataset rewrite.
