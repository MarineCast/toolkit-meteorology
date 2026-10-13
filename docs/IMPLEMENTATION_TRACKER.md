# Remaining meteorology implementation tracker

## 2026-10-04 bounded live-week documentation demo

Added `meteorology demo-hourly-week` with a write-free/network-free preview,
persistent request/payload limits, single-writer locking, exact HTTP ranges,
strict shared GRIB decoding, atomic retained bundles and receipts, daily frozen
releases, both summary scopes and readable/JSON resource reports.
See the [reproduction guide](live-week-demo.md) and [executed results](live-week-results.md).

Implementation: **PASS**, 230 tests passed and one separate opt-in legacy live
HRRR/f01 test skipped. Ruff, catalog/policy checks, strict MkDocs and diff checks
passed. Regular wheel checks outside the source checkout passed for one live
cycle, zero-request cycle resumption, and the complete week's cached rerun.
Existing dependencies were reused; fresh dependency resolution was not tested.

Source compatibility: **PASS for January 1–7, 2024 within the packaged example box**.
168 actual f00 analyses produced 75,600 rows across 450 H3 R5 cells. All seven
frozen daily releases and both summary outputs validated. Main acquisition/build/
export took 1,173.3 seconds and peaked at 965.5 MiB RSS. Both-scope summary Parquet:
639,983 bytes / 32,472 rows; region-only: 32,662 bytes / 72 rows. Full retained
workspace/evidence/summary/metadata storage was approximately 426.5 MiB. The
measurement record states the development retry and separate wheel-probe accounting.

Empirical scientific acceptance and forecast skill: **NOT_RUN**. No new observation
comparison, source-era study, precipitation/astronomy integration, north-of-49.70°N
qualification, retention cleanup, OrcaCast integration, or remote publication was done.
This closes the bounded real-data demo milestone, not the broader M2–M7 study.

## 2026-10-04 compact daily/weekly summaries

Added `meteorological.weather_summary` / `hourly_sample_daily_weekly_summary_v1`
with `export-weather-summary` and the public `export_weather_summary()` API.
The default publishes both native-H3 and whole-region sampled statistics into
one Zstandard-compressed Parquet, with typed UTC period boundaries, explicit
coverage/status, full calendar-week denominators and optional as-of filtering.
The exporter processes one day at a time and retains only current-week numeric
accumulators; source lineage metadata grows with the number of input days.
Artifact H3 identity collection now iterates bounded batches instead of reading
an entire output column. Source grids and old products are not deleted or changed.
See [the contract and synthetic resource check](weather-summaries.md).

Hourly retained inputs now have a precipitation-free versioned decoder schema;
legacy raw bundles remain readable and their unused PRATE is ignored. Required
atmospheric fields still reject missing/non-finite values. The decoder can request
only atmospheric fields with `include_precipitation=False`; the default daily
route is unchanged. Precipitation interval helpers reject contradictory supplied
step bounds or valid times. No new precipitation amounts are published.

Implementation evidence: `PYTHONPATH=src python -m pytest -q -p no:cacheprovider`
passed **220 tests**, with one opt-in live-provider test skipped. Ruff, catalog and
feature-policy freshness, generated variable-documentation comparison, strict
MkDocs, and `git diff --check` passed. A wheel built with `--no-build-isolation`
was installed using `--no-deps --target` outside the checkout and exercised against
the frozen synthetic DST fixtures using existing dependencies; this is not a fresh
dependency-resolution check. Both summary scopes passed installed-wheel validation.
The source tests also cover summary freezing/relocation after hiding original inputs.

At this summary-only checkpoint, source compatibility was **NOT_RUN** for the new
precipitation-free real GRIB request; the later live-week evidence above supersedes
that status for its explicit cycles and bounds.
Empirical acceptance: **NOT_RUN** for regional accuracy or forecasting skill.
At that checkpoint, live hourly acquisition and real-data performance measurement
were still pending; the demo above now covers them for one week. Precipitation/
astronomy integration and OrcaCast integration remain separate work. The older M2–M7 qualification gates below are not closed by this export.

## 2026-10-03 seven-day offline demo and review corrections

The local continuation from `88cf2815bcde1bcd8e7764c9272d5ce24b95c908`
now rejects changed hourly support/bounds before build and checks product,
acquisition and support spatial declarations during deep validation (R1).
The public window helper reads published uppercase timestamps directly and
rejects conflicting lowercase aliases (R2). Retained decoded grid/sidecar
pairs stage and validate within a candidate directory, then commit by one
directory rename under a per-hour lock; valid equal bundles can be reused and
orphan candidates do not block retry (R3). Exact precipitation intervals now
declare forecast versus retrospective kind, enforcing initialization and as-of
rules without rejecting an available forecast solely because its valid end is
in the future (R4). No new precipitation product or method ID is claimed.

The `demo/hourly_week_offline.py` run used seven separate local-day producer
cycles for 2024-01-02 through 2024-01-08 in the example Pacific support.
It generated 168 synthetic f00 hours and 75,600 H3-hour rows over 450 cells.
Every daily acquisition, product and frozen copy passed validation; the
selected-cell weekly summary had 168/168 available sampled hours. The
combined Parquet is explicitly a demo aggregation, not a new weekly release.
Provider requests and network bytes were both zero. Implementation gate:
PASS for this synthetic offline scope. Real HRRR source compatibility:
NOT_RUN. Empirical scientific acceptance: NOT_RUN. A budgeted real-source
week and matched observational study remain the highest-priority gates.

The package suite passed `209 passed, 1 skipped` (the skipped test is the
opt-in live HRRR test). Ruff, catalog/policy freshness, generated variable
documentation comparison, strict MkDocs build, wheel/sdist build with an
available local setuptools environment, Twine checks, and an outside-checkout
installed-wheel seven-day run passed. The isolated build attempt could not
fetch setuptools in the network-restricted environment; the successful
fallback used local setuptools 80.9.0 with `--no-isolation`.

## 2026-10-02 focused continuation

Candidate-contract regressions were reconstructed from `NEXT_CODEX_TASK.md`; its referenced
`evidence/test_review_candidate_contracts.py` was not supplied. Four reconstructed tests failed
against the installed editable package before repair: an interval with no spatial point/cell was
accepted; an f00 availability preceding its valid time was accepted; an omitted RONI publication
raised `KeyError` on an as-of query; and a whole-hour-duration window offset by one second was
accepted. The repaired tests now pass. Native precipitation intervals require a grid-point index;
mapped H3 intervals require a cell and mapping identity. Totals permit independently verified
adjacent intervals from different cycles at the same target; cumulative differencing still
requires one run and retains the later of both input availability timestamps. An f00 hourly row
requires the declared assumed-lag availability policy and cannot be available before valid time.
This is contract validation, not real-source compatibility or scientific acceptance.

The separate M3 hourly R5 family now has an offline retained-decoded-grid
acquisition, immutable normalized-grid/sidecar/sample/crosswalk objects, a
complete UTC-hour inventory, a pinned hourly build, versioned Arrow/method
identities, CLI/API/catalog/discovery, deep source-to-H3 validation, and
freeze/relocation. Synthetic offline tests execute 23/24/25-hour days,
missing-hour/cell, calm wind, availability, checksum, interruption and retry
cases. The raw decoder route uses the existing normalized HRRR grid schema;
no real GRIB messages were retrieved or accepted as provider evidence here.

Date: 2026-10-02. Original roadmap checkout: `f6228703e06b4768dd274404883157d746781a18` on clean `main`; focused continuation starts from the clean, pushed local commit `6957cba45aa6c775a6b7d30e3530180ae55005c7` on a new local `codex/meteorology-hourly-continuation` branch. This tracker records tested code separately from actual source support and independent scientific acceptance. It does not turn a passing synthetic contract test into a real-source claim.

| Milestone | Implementation | Source compatibility | Empirical scientific acceptance | Affected output and exact remaining gate |
| --- | --- | --- | --- | --- |
| M1 O01 | IMPLEMENTED: source-manifest inventory binding, row-bound native-validation evidence, shared publication gate and negative tests | PASS for the existing current manifest contract and offline fixture; historical/unevidenced source rejected | NOT_APPLICABLE_WITH_REASON: publication integrity, not weather accuracy | Existing acquisition snapshots; real older releases keep their original claims and matching validator |
| M1 O02 | IMPLEMENTED: rebuild verifier uses shared 700–1200 hPa hard limits and 800–1100 hPa regional warnings; structural failures separate | PASS for package fixtures; 750/1150 hPa are synthetic contract cases only | NOT_APPLICABLE_WITH_REASON: pressure screening policy, not local plausibility | Existing rebuild report; realistic regional pressure distributions remain a scientific study |
| M1 O03 | IMPLEMENTED: per-format/per-field NDBC fills before ranges, rejected-token provenance and frozen pilot recomputation | PASS for the frozen NDBC historical standard meteorological slice and authoritative format definition | NOT_RUN for regional accuracy; existing one-buoy/day pilot metrics unchanged | NDBC comparison report; broaden sites/eras and height/interval matching |
| M2 | IN_PROGRESS: frozen USNO, JPL and NDBC comparisons rerun; machine-readable study scope and coverage gap recorded | PARTIAL: one prior decoded HRRR example cycle; full requested domain, eras and references unverified | BLOCKED: prespecified regional inputs, thresholds, holdouts and decoded source evidence absent | `validation/study_spec_v1.json`, coverage report, retained pilot reports; no precision-horizon claim |
| M3 | IMPLEMENTED for retained decoded input: separate f00 hourly H3 family with immutable acquisition, pinned build, CLI/API/catalog, deep validation and freeze/relocation; synthetic 23/24/25-hour tests | NOT_RUN for real provider: no representative full-core 24-hour decoded GRIB source inventory or archive-era fixtures | NOT_RUN for atmospheric accuracy | No live acquisition was authorized; method `hrrr_f00_hourly_h3_r5_v1` and schema `hourly-weather-r5-v1` do not certify real-source compatibility |
| M4 | IN_PROGRESS: exact nonoverlapping accumulation arithmetic and same-run cumulative differencing with run/grid/step/reset guards, dry zero, gap/overlap, source and DST tests | BLOCKED: representative real accumulation parameter, level, units, step range, era and grid evidence absent | NOT_RUN | Candidate interval helpers only; legacy `PRECIP_MM_DAY_ESTIMATE` unchanged |
| M5 | IN_PROGRESS: matched-height dewpoint depression and calm-aware gust factor helpers | BLOCKED: verified dewpoint/cloud-layer/ceiling/radiation source fields and archive-era fixtures absent | NOT_RUN | No new variables in the published schema/catalog; no inferred fog probability |
| M6 | IN_PROGRESS: current-state and safety documentation updated; offline suite, build and wheel smoke recorded below | PARTIAL: fresh macOS Python 3.12 wheel and synthetic workflow passed; Linux and acquisition extras were not run | NOT_APPLICABLE_WITH_REASON: operating evidence is product-specific | No release/tag/push, no scheduled acquisition or object deletion |
| M7 | IN_PROGRESS: source-specific native-grain CPC RONI retrospective/as-of selection and revision tests | BLOCKED: archived CPC publication vintages, real retained ERA5/ECCC fixtures, source rights and budgeted acquisition absent | NOT_RUN | RONI helper has no published climate product; ERA5/ECCC adapters and overlap report remain unimplemented |

## Method and compatibility decisions

- The six-snapshot daily producer and its method IDs remain unchanged. New helper contracts are not silently substituted into the daily matrix or catalog.
- Acquisition publication now records `acquisition_evidence_policy=row-bound-current-source-v1`. A current verified pre-change acquisition manifest can be reused under its existing spatial contract; a workspace marker or orphan working inventory cannot certify legacy data. Newly fetched rows retain a support- and row-bound evidence record for interrupted-run resumption. Source objects remain immutable.
- Rebuild pressure hard acceptance follows `meteorology-field-contract-v1`. The 800–1100 hPa interval is diagnostic. Synthetic 750/1150 hPa tests are contract probes, not assertions about expected local weather.
- NDBC parsing supports declared historical standard-meteorological numeric fills and realtime `MM` separately. NOAA's [measurement description](https://www.ndbc.noaa.gov/faq/measdes.shtml) defines the format; [NDBC standard meteorological metadata](https://dods.ndbc.noaa.gov/thredds/dodsC/data/stdmet/46012/46012h2026.nc.html) identifies WSPD/GST `99.0`, WDIR `999`, ATMP `999.0` and PRES `9999.0` as fills. No value of 99 is globally replaced.
- The interval helper accepts only preverified millimetre `accum` records with exact nonoverlapping bounds. It neither infers amount from PRATE nor asserts that a string field alone verifies a GRIB step range. Hourly and interval availability times are treated as provided metadata; actual provider publication times remain unmeasured.
- CPC's [RONI definition](https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/) currently uses an ERSSTv6 three-month relative Niño 3.4 index with a 1991–2020 base and possible recent revisions. The helper uses the explicit identity `RONI_ERSSTv6_1991_2020`; unknown historical publication times mean retrospective-only. It does not alias ONI or Niño 3.4, infer phases, or create a per-H3 climate table.

## Evidence and execution

The O01 negative snapshot regression was run against the unmodified `f622870` source through `PYTHONPATH`: it failed because the baseline snapshot accepted a manifest whose spatial-policy claim had been changed. The repaired source rejects it before candidate publication. The O02 full fixture acquisition → daily build → rebuild-verifier test was likewise run against `f622870`: both synthetic 750 and 1150 hPa cases built but failed in the baseline verifier's 800–1100 hPa gate; both pass the repaired verifier with regional warnings. A direct `f622870` NDBC parser call returned WSPD/GST `99.0` as measurements; the repaired parser returns missing with explicit source-token reasons. These are executed baseline reproductions, not inferred from the review alone.

The frozen NDBC source slice hash is `e4cdab9abc502f0b2c3ee2fa5289cb8c1343573fd6ebd4da0f3f4ca70eb97bf5`; the retained HRRR point-sample hash is `8301892d7be82cce42d7ebda2cd4bdeedc878fba641d0660c1a933029d1eb104`. Recomputed NDBC temperature, pressure, speed and gust metric counts remain 24; non-calm wind-direction count remains 22. All numerical metrics are unchanged from the original report. The new report records 120 ten-minute rows excluded by the exact-hour rule; none of its 24 exact-hour rows has a source-defined missing field token. The pilot therefore shows no demonstrated prior metric contamination. Tests separately show that an exact-hour WSPD/GST `99.0` would be excluded from metric denominators.

The existing USNO and JPL reports were rerun from their frozen reference JSON and matched the checked-in files. They remain coarse astronomy method evidence; their hourly UTC diagnostic does not certify the production 30-minute local-day integration or precision viewing thresholds. Repository files `validation/study_spec_v1.json` and `validation/reports/coverage_scope_v1.json` retain the 50.00°N requested OrcaCast model area and the smaller 49.70°N demonstrated example separately. Broader regional comparison is NOT_RUN.

On macOS ARM with Python 3.12.14, a fresh outside-checkout environment resolved and installed the wheel with `pip check` reporting no broken requirements. An installed-wheel `meteorology --workspace ... init` and `example-offline` generated a synthetic one-day 514-row R4/R5 matrix and validated its acquisition, weather, daylight, lunar and matrix manifests. The independent `validate --daily-matrix` returned exit 0. The synthetic workspace occupied 776 KiB (`du -sk`); the wheel occupied 196 KiB. A `/usr/bin/time -l` wrapper printed 31.06 seconds elapsed but returned exit 1 after the successful example because sandboxed `sysctl kern.clockrate` was denied; that wrapper is not a clean command pass. Peak memory and provider requests/bytes were not measured. These numbers do not extrapolate to the full region or real source processing.

No NOAA/ECCC/ERA5 provider acquisition was requested or run in this task. No full-region resource estimate, atmospheric accuracy claim, climate publication-vintage reconstruction or binational agreement result is available. The final execution handoff records exact commands and tree fingerprint.

### External inputs needed for the blocked gates

- M2: retained decoded HRRR GRIB messages or legally accessible hash-addressed bytes for selected source eras and locations; prespecified reviewed NDBC/NCEI/ECCC site-period archives with historical station coordinates, sensor heights, averaging intervals and QC; independent near-horizon reference responses matching the production local-day integration. The existing single-cycle/single-buoy files do not provide those groups.
- M3: a full local civil day of 23/24/25 distinct `sfc/f00` GRIB analyses with all nine core fields at a verified native grid and retrieval identity. The existing 24-sample point pilot omits humidity, visibility and cloud, and is not an H3 hourly inventory.
- M4: real source accumulation GRIB messages over representative early/middle/recent archive cycles with parameter/level, units, step type/range, initialization, valid time and native grid retained. The existing f01 PRATE snapshots do not meet this input contract.
- M5: representative archived source messages and authoritative definitions for 2 m dew point, low/mid/high cloud fractions, cloud base versus ceiling, and downward/beam/diffuse radiation. None of these are established by the current nine-field decoder.
- M7: retained ERA5 CF/GRIB source files and dataset/version/revision metadata, retained ECCC HRDPS/HRDPA messages and rights/coverage metadata, and archived CPC RONI table publication vintages. Current CPC table values alone cannot reconstruct historical as-of availability. No credential or live request/cycle/byte budget was supplied for new provider acquisition.

## Prioritized residual backlog

1. Retain representative real `sfc/f00` decoded GRIB bundles across supported eras with source metadata and an explicit provider request/cycle/byte budget, then run the hourly producer and compatibility/observational comparison. The offline hourly publication path is implemented; real source compatibility is not established.
2. Verify actual HRRR accumulation messages and archive semantics, then wire a separate interval-amount producer with strict local-day totals, as-of policy and source-compatible tests.
3. Verify dewpoint, cloud layers/base/ceiling, radiation and solar-angle sources/algorithms before expanding schemas or advertising those fields; add matched observational qualification.
4. Freeze site/time/height/QC thresholds and source bytes for the regional study, quantify requested-versus-supported geography, and run independent observational and astronomy product-level comparisons with holdouts.
5. Complete outside-checkout wheel/platform smoke and bounded resource measurements, then make a product-specific release decision. No release is authorized here.
6. Obtain retained real ERA5/ECCC fixtures and CPC publication-vintage evidence before provider adapters, binational overlap results or historical as-of climate claims.
