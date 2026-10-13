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

Baseline Ruff and strict MkDocs passed after installing the declared development
extras. First point-block result: **43,264 records read**, exactly once per block,
676 physical row groups; one decode stream. 1.985 s, 305,053,696 bytes current/peak
RSS, 3,220,992 retained bytes. The small row groups trade Parquet metadata/storage
for selective reads; this workload uses about 3.3 times the previous storage.
There is no timing threshold or claimed real-provider speedup.

## Final equivalent-workload comparison

The final replay ran the original executor from a read-only `git archive fef8fcf
src` snapshot via `PYTHONPATH`, then the owner feature checkout, using the same
benchmark script, generated frames, Python environment, 256 points and 169 messages.
Both produced byte-identical daily Parquet:
`4f0fc4346bedd5a6392cd78a455e0bc53e4a54d87919fccfd1cb6ae528914675`.

| Measurement | Original executor | Refactored executor |
| --- | ---: | ---: |
| Normalized records read | 173,056 | 43,264 |
| Selected physical row groups read | full-scan batches | 676, each once |
| Source decode streams | 1 | 1 |
| Daily output rows | 256 | 256 |
| Wall seconds | 3.258 | 3.408 |
| Current RSS at completion, bytes | 277,282,816 | 280,690,688 |
| Historical peak RSS, bytes | 277,282,816 | 280,690,688 |
| Retained workload bytes | 983,194 | 3,220,991 |
| Provider requests / transferred bytes | 0 / 0 | 0 / 0 |

This is **75 percent fewer normalized records read**, not a demonstrated wall-time
speedup. Timings varied across runs and the last replay was slower. Small selective
row groups incur storage/footer overhead, about 3.27 times the original retained
bytes in this tiny fixture. The footer is now opened once per normalization file.
Future larger-workload layout tuning should preserve the operation-count and
bounded-memory tests; no general resource or real-provider speedup is claimed.

The fixed ERA5 pilot now decodes each delivered file once: the mocked concrete
HTTP/independent-QA regression instruments two source files and observes two decode
streams. The former grid-check predecode has been removed; unexpected grids are
rejected before staging in the processing stream.

Concrete mocked-HRRR public-getter evidence: a temperature UTC day makes 48 calls
(24 inventories plus 24 temperature-only byte ranges). A following wind request
reuses all inventories and adds 48 U/V field calls. A subsequent combined request
reuses those source bytes and adds zero calls. A final compatible result hit adds
zero calls and performs no decode. These are generated-message/mock-transport
measurements, not NOAA transfer sizes. Whole nationwide selected GRIB messages
remain indivisible. ERA5 temperature submits only `2m_temperature`; a final hit
works after removing the fixture token and makes zero calls.

## Final validation scope

The owner suite runs on local macOS/Python 3.12; the existing opt-in NOAA test is
skipped. Linux Python 3.11/3.12/3.13 CI remains configured and a macOS 3.12 smoke
job was added; hosted jobs were not run or claimed passing.

Fresh regular-wheel checks ran outside the checkout with independently resolved
libraries. Core import of every module, public getter planning, CLI discovery and
legacy synthetic weather/daylight/lunar/matrix generation passed with **no Folium,
Branca or GeoPandas installed**. After adding the ERA5 extra, installed public
getters processed generated GRIB temperature/snowfall, exported durable metadata
and reused a final cache with zero requests. `pip check` passed in both stages.
The fresh wheel used newer libraries (including ecCodes 2.49, Arrow 26 and H3 4.5)
than the development environment; no real-provider payload was acquired.

Source family methods, local-day/DST products, production study gates and existing
releases were not rewritten. The new standalone request method/schema are v1.
No credentials, local caches, generated datasets or benchmark outputs were committed.
No main merge, release, protected-ref operation, data deletion or remote branch push
was performed. Only toolkit-meteorology changed.

Final owner command `.venv/bin/python -m pytest -q`: **433 passed, 1 skipped in
174.71s**. The skipped test is the opt-in NOAA acquisition test. All documented
non-network lint, docs, parity, build, packaging and installed-wheel checks passed.
