# Request API refactor evidence

Baseline: fetched `origin/main` at `fef8fcf`; owner checkout clean on `feat-demo`
(`256df38`), preserved as a separate branch. Work starts on
`codex/meteorology-request-api`. No sibling repositories changed.

## Execution trace and baseline

- Legacy: root API / CLI → `surface_weather.download` → Herbie combined nine-field
  f00 plus matched f01 rate acquisition → native identity/footprint/rotation checks
  → cached crosswalk/R5 samples → inventory → `surface_weather.build`. Six local
  samples and rate-estimate meaning stay unchanged, including DST behavior.
- Hourly: `hourly_weather.live` / `week_demo` acquire eight f00 fields;
  `hourly_weather.product` ingests complete retained grids and publishes the
  separate hourly family. `native_day` instead has exact fixed pilot dates/bounds,
  HTTP range receipts and `native_source.decode_hour`; this source decoder and
  `NativeHTTP` are reusable; the exact pilot plan and QA are qualification fixtures.
- ERA5: `planning.request_plan` and fixed `pilot.pilot_plan` → `transport` /
  `jobs.run_job` → `source.decode_grib` normalization → `execution.process_native_day`
  → `daily.utc_daily` → fixed pilot QA. Production study and reporting-mask gates
  are independent of generic rectangular request semantics.

Verified main findings: full normalized Parquet reread once per 64-point batch;
pilot preliminary decode plus processing decode; repeated ERA5 coordinate sorting;
HRRR geometry reused across fields but recomputed across hours; `ru_maxrss` used as
current RSS; legacy downloader submits every permitted timestamp immediately.
Existing crosswalk cache and vectorized legacy aggregation remain active.

Python 3.12 macOS baseline: `.venv/bin/python -m pytest -q`:
**392 passed, 1 skipped in 107.04s**. Skipped opt-in live NOAA test.
Development venv initially shares the existing conda scientific libraries;
final wheel validation uses a fresh independent venv.

`benchmarks/native_processing.py` processes synthetic normalized messages via the
actual bounded executor (mocked decoder/zero network). 256 points, 169 messages,
43,264 records, four aggregation batches. Before: **173,056 records read**, one
stream decode, 2.240 s, 279,019,520 bytes historical peak RSS, 983,194 retained bytes,
256 daily rows. Timing and memory are environment-specific; operation counts
are the deterministic regression criterion. This does not measure provider speed
or establish observed regional accuracy.
