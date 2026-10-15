# toolkit-meteorology agent guidance

Regional default API changes: read `docs/regional-api.md`; relevant offline checks
are `python -m pytest -q tests/test_regional.py`, then the full suite, ordinary lint,
strict docs, distribution and outside-checkout installed-wheel checks. Tests must
never access ECMWF. User instructions to avoid execution override these commands.

## Scope and current state

Historical and forecast meteorological data products.

This repository contains the installable `meteorology` package extracted on 2026-09-15.
Read `docs/ARCHITECTURE.md` for ownership/import changes; `docs/CONTRACTS.md` for scientific
or product changes; `docs/WORKFLOWS.md` before producer execution; README.md and
docs/MIGRATION.md for setup or extraction-history questions.
Preserve unrelated changes and read deeper instructions before editing a subdirectory.

## Shared MarineCast context

For boundary, dependency, shared schema/provenance, or application-integration changes, read
[INFRASTRUCTURE.md](https://github.com/MarineCast/.github/blob/HEAD/INFRASTRUCTURE.md).
In the grouped workspace use `../../.github/INFRASTRUCTURE.md`; in a flat clone layout use
`../.github/INFRASTRUCTURE.md`, resolving from this checkout. Otherwise use the linked copy;
if unavailable, report the limitation without inventing a shared standard. Sibling instructions
are not inherited. Local implementation contracts remain authoritative; surface conflicts.

## Domain contracts

Keep observed, reanalysis, and forecast products distinct. Preserve valid time, forecast issue
time and lead time where applicable, time zone, accumulation interval, units, and provider/grid
provenance. Validate spatial and temporal interpolation support; unsupported values stay missing.
Weather measurements are not sighting probabilities.

## Implementation boundaries

- Keep this toolkit species-neutral and independently installable; do not require an OrcaCast
  checkout or import through sibling filesystem paths.
- Before adding a pipeline, define source rights, input/output grain, units, spatial and temporal
  support, missingness, provenance, and validation in the repository documentation.
- Add dependency declarations and runnable setup/validation commands with the implementation;
  do not copy viewshed's GDAL stack or commands unless this toolkit actually needs them.
- Prefer deterministic calculations and small synthetic/offline fixtures. Keep credentials,
  downloads, and large generated products out of tracked source.
- Keep unknown and unavailable values distinct from observed zero. Validate uniqueness and join
  cardinality rather than silently dropping conflicting records.

## Validation and completion

Use isolated Python 3.11+: `python -m pip install -e '.[test]'`.
Start behavior validation with `python -m pytest -q tests/<relevant_test>.py`, then the required
suite below. Documentation-only edits need reference checks and `git diff --check`, not Python tests.
No local skills are needed yet; load only the task-specific documents routed above.


For documentation-only work, inspect `git status --short` and the diff, verify references, and run
`git diff --check` from this repository. Run `python -m pytest -q` after installing `.[test]`. Verify regular wheel installation
from outside the checkout. Keep config/ and packaged configuration resources synchronized.
When adding executable behavior, add appropriate checks and document their exact commands here.
Report tests actually run, unverified source acquisition, and any unrun integration paths.

## Codebase navigation

Use the existing local `graphify-out/graph.json` for structural questions; skip graph work for
obvious targeted edits. Prefer the smallest useful retrieval:

1. Known symbol: `explain` first, then follow only relevant callers/callees/imports/dependencies.
2. Use direct relationships before deeper traversal; broad `query` is for unknown ownership.
3. If results are large or truncated, narrow the symbol or relationship before increasing budget.
   `--budget` is not a guaranteed cap. Avoid unnecessary depth-2 neighborhoods.
4. Read the identified source/tests. They outrank schemas/contracts, architecture docs, then the
   graph. Use targeted `rg` when graph coverage or freshness is insufficient.

```bash
graphify explain "build_surface_weather"
graphify affected "build_surface_weather" --relation calls --depth 1
graphify query "<topic>" --context call --budget 1500
```

`affected` follows reverse edges (callers); `explain` shows immediate connections. Use ast-grep
for syntax patterns and `rg` for literal/config/prose/filename searches. Read architecture for
ownership/import changes, not every typo. Optional shared setup/examples:
[code navigation](https://github.com/MarineCast/.github/blob/HEAD/docs/code-navigation.md)
(local grouped workspace: `../../.github/docs/code-navigation.md`). No sibling checkout is required.

Graphify is optional developer tooling (`graphifyy==0.9.62`, isolated Python 3.10+), not a runtime
dependency. Create a missing graph or refresh after structural edits, from this checkout only:

```bash
graphify extract . --code-only --no-cluster
```

Do not rebuild merely for a question. Check intentional deletions before using `--force` to bypass
shrink protection. `.gitignore` and `.graphifyignore` apply; AST-only extraction does not index
prose semantically. Graphs/caches are disposable local-only files: never commit or publish them.
Any future sharing requires an explicit policy covering destination, revision, freshness and review.
Global skill defaults do not override repository scope or code-only extraction. Do not run
`graphify codex install` over maintained instructions or enable hooks/merge drivers implicitly.

Daily matrix behavior: `PYTHONPATH=src python -m pytest tests/test_daily_matrix.py -q`.
Contract and CLI are documented in `docs/daily-matrix.md`.

Compact weather summary and input-contract checks: `python -m pytest -q
tests/test_weather_summary.py tests/test_hourly_weather_product.py
tests/test_precipitation_intervals.py`. See `docs/weather-summaries.md` for
temporal/spatial support, availability and memory boundaries; these exports do not
qualify future forecast skill or real provider accuracy.

Roadmap PR-01 through PR-04 checks: `python -m pytest -q
tests/test_field_contracts.py tests/test_independent_validation.py
tests/test_source_and_catalog.py tests/test_production_contracts.py` and then
`python -m pytest -q`. The bounded NOAA compatibility test is opt-in:
`METEOROLOGY_LIVE_HRRR=1 python -m pytest -q tests/test_live_hrrr.py`.

Live-week budget, range-response, resumption and dry-run checks:
`python -m pytest -q tests/test_live_week_demo.py`. Read `docs/live-week-demo.md`
before executing `meteorology demo-hourly-week`; ordinary tests never acquire data.

Shared-study selection/provenance checks: `python -m pytest -q tests/test_shared_study.py`
then the full suite and installed-wheel preflight from outside the checkout.
See `docs/shared-study.md`; proposed geometry and pending registries reject production.

For changes to separate retrospective source helpers, run
`python -m pytest -q tests/test_era5.py tests/test_shared_study.py` followed by
the full required suite and an installed-wheel planning check. ERA5 GRIB fixture
checks need the `era5` extra; no provider or credential access is required.

Bounded native source pilot regression checks: `python -m pytest -q
tests/test_era5_resources.py tests/test_marine_reporting.py tests/test_era5_jobs.py
tests/test_era5_transport.py tests/test_era5_pilot.py`, then the full suite and
regular installed-wheel check outside this checkout. See
`docs/CDS_JOB_INTEGRATION_AND_PILOTS.md`. The tests use mocked HTTP and generated
GRIB only. `python -m meteorology.era5.pilot plan --study-config ...` is offline;
`run` performs provider access and requires the independently reviewed exact plan.

Separate bounded native HRRR UTC-day pilot: install `.[test,native-pilot]`, run
`python -m pytest -q tests/test_hrrr_native_day.py tests/test_hrrr_native_source.py`,
then the full required suite and installed-wheel checks from outside the checkout.
Read `docs/HRRR_NATIVE_DAY_PILOT.md` before execution. Mocked HTTP and generated
GRIB tests do not qualify actual NOAA archive coverage. `native_day plan` is offline;
`native_day run` uses the exact reviewed source/date/bbox/caps and private cache.

Standalone request changes: install `.[dev,native-pilot]`, run
`python -m pytest -q tests/test_request_api.py tests/test_request_transport.py
tests/test_processing_scaling.py tests/test_concurrency.py`, then the full suite,
`ruff check src tests benchmarks demo`, strict docs and wheel/sdist checks.
Generated GRIB tests skip when the optional codec is absent; CI installs it.
Core installed-wheel smoke must run outside the checkout without Folium, Branca
or GeoPandas and exercise source/variable/get planning plus legacy offline products.
Use `benchmarks/native_processing.py` for deterministic read/decode counts; timing
is environment-specific, and no routine test contacts NOAA/CDS.

Request lifecycle checks: `python -m pytest -q tests/test_request_lifecycle.py
tests/test_request_commit.py`, then the focused request suite and full required
checks above. `python benchmarks/request_lifecycle.py` uses generated GRIB and
mocked transport, asserting operation counts rather than timing thresholds.
`demo/qualify_requests.py` defaults to offline planning; `--run` explicitly opts
into bounded public-getter provider qualification after reference/terms setup.

Historical backfill checks: `python -m pytest -q tests/test_backfill.py
tests/test_request_lineage.py tests/test_era5_resources.py`, then the full checks
above and a multi-year installed-wheel dry run. Read `docs/era5-backfill.md` before
acquisition. `benchmarks/era5_backfill.py` defaults to generated GRIB/mock transport;
`--live` requires configured CDS credentials and manually accepted terms.
`benchmarks/era5_arco.py` performs bounded public metadata access by default and
selected hourly transfers only with `--run`; its output is exploratory evidence,
not an adopted backend or production qualification.
`python benchmarks/era5_aggregation.py --help` describes a local retained-source
replay with exact all-column/lineage comparison against an exported prior reducer;
see `docs/era5-backfill-performance.md` for the bounded command and timing scope.
Backend benchmark changes: `python -m pytest -q tests/test_backend_benchmark.py`,
then the full suite and ordinary lint/docs/distribution checks. The live
`benchmarks/era5_backend_comparison.py` phases are explicit acquisition: two UTC
days, seven fields, four workers, separate processes and local caches. Read
`docs/era5-backfill-performance.md` for caps, numerical comparison and limitations.
