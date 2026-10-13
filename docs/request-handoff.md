# Request refactor handoff

Work is on local branch `codex/meteorology-request-api`, based on fetched
`origin/main` at `fef8fcf`. The clean original `feat-demo` branch at `256df38` is
preserved. Main and published datasets were not changed; no remote push or release
was performed. Only this repository is affected.

## Implemented changes

All variable getters delegate to one validated request planner and executor.
Dependencies propagate to NOAA exact field ranges and CDS variable selections.
Results retain native or qualified HRRR R5 support, UTC boundaries, units, coverage,
nulls, source receipts, actual forecast initializations/leads, dependency versions
and method/schema/producer fingerprints. ERA5 snowfall is an implemented hourly
water-equivalent accumulation, not snow depth. Cache writes are serialized,
immutable and atomic; complete repeats/subsets avoid providers, partial results
are not successful hits, and corrupted retained artifacts fail explicitly.

ERA5 point batches read addressed physical row groups once. Grid verification is
inside the pilot's sole normalization stream. Geometry/crop/rotation work is reused
only after message grid verification. Current RSS admission is separate from peak
reporting; the legacy downloader bounds its submitted queue and cancels pending
work at interruption. Existing complete-family validation remains intact.

## Examples, support and compatibility

[Weather requests](request-api.md) contains working Python/CLI syntax, the complete
source × variable × output support matrix, setup, cache behavior and actionable
errors. From a clone, `python demo/request_offline.py --output ./request-demo`
uses generated GRIB through the public API without credentials or provider access.
The README leads with installation, Python, CLI, returned data and availability.

HRRR supports temperature, wind, cloud, sea-level pressure, RH and visibility in R5
or native output. ERA5 supports temperature, wind, cloud, sea-level pressure, RH,
dewpoint, precipitation and snowfall in native output. Supported quantities offer
daily/hourly output. HRRR accumulation/snowfall/dewpoint and ERA5 visibility/H3
remain unsupported. The selective legacy-rate-estimate helper reports its limitation
and points to the retained complete-family API. Queries are capped at seven UTC days,
a bounded crop, one worker and explicit budgets. HRRR is restricted to its qualified
v4 CONUS decoder era. ERA5 H3, earlier HRRR decoder eras, broader resource workloads,
authenticated CDS/new live NOAA compatibility and regional independent accuracy
remain unverified or unimplemented as specified in the support matrix.

The standalone request method is `meteorology-selective-utc-request-v1`, schema
`meteorology-date-support-metric-v1`; family method/schema versions retain their
old meaning. Shared-study publication still requires its original approvals and
materialized registry/mask. No migration of retained products is needed.

## Cleanup and performance

[Cleanup inventory](request-cleanup.md) records callers and decisions. The redundant
private credential wrapper was removed; CLI dispatch and point-block writing now
have one implementation each. Folium/Branca/GeoPandas moved to `visualization`,
with lazy rendering imports. Config/resource parity remains enforced. Advanced
migration, validation, benchmark and fixed qualification pilots remain active.

[Performance evidence](request-refactor-evidence.md) contains the baseline, trace,
measurements and reproducible commands. The final synthetic comparison reduces
normalized reads from 173,056 to 43,264 and preserves daily Parquet bytes. Wall time
was 3.258 versus 3.408 seconds; no elapsed-time speedup is claimed. Current/peak
RSS was 277,282,816 versus 280,690,688 bytes and retained storage 983,194 versus
3,220,991 bytes. Mocked getter transport proves selective acquisition and zero-call
final hits. These are software fixtures, not provider/regional measurements.

## Validation commands

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests benchmarks demo
.venv/bin/mkdocs build --strict
.venv/bin/meteorology catalog --check
.venv/bin/meteorology feature-policy --check
.venv/bin/python -m meteorology.variables --markdown > /tmp/meteorology-variables.md
diff -u docs/reference/variables.md /tmp/meteorology-variables.md
.venv/bin/python -m build --no-isolation --outdir /tmp/meteorology-request-dist
.venv/bin/python -m twine check /tmp/meteorology-request-dist/*
git diff --check
```

Baseline was **392 passed, 1 skipped in 107.04s**. Final owner suite: **433 passed, 1 skipped in 174.71s**. The skipped test
is opt-in live NOAA acquisition. Ruff, strict documentation, catalog/policy/variable parity,
wheel/sdist build and Twine passed. Fresh installed-wheel core and ERA5-fixture
checks outside the checkout passed, as did `pip check`. Generated-GRIB and
mocked-transport coverage includes temperature for both adapters, HRRR default R5
sampling, selected dependencies, wind rotation/reductions, all ERA5 instantaneous
quantities, precipitation/snow units, nulls/dry input, calendar boundaries/expver,
cache subsets/corruption/invalidation/concurrent writers, operation counts and
CLI/Python/help parity. Existing local-day/DST tests remain passing.

The opt-in NOAA test remains skipped. Actual CDS access, real new query payloads,
regional/native historical rebuilds, independent scientific comparisons and hosted
CI are unrun. macOS smoke was added without removing the Linux matrix.

## Commits

| Commit | Work package |
| --- | --- |
| `6a75db0` | Baseline and deterministic processing benchmark |
| `9ea7ecc` | ERA5 selective reads, single pilot decode, resident-memory admission and queue bounds |
| `cbc55ba` | Verified HRRR geometry reuse and selective fields |
| `13b22bb` | Shared request planning, capabilities and public getters |
| `01ac7b9` | Selective adapter execution |
| `1fdb3fb` | ERA5 snowfall normalization and source fixtures |
| `3573ac3` | Shared immutable cache and disk-backed results |
| `f049188` | CLI discovery and shared credentials |
| `7bb2625` | Forecast lineage, version provenance and conservative selective preflight |
| `bf4e6cd` | Cleanup, reusable active helpers, optional visualization and source packaging |
| `3f29317` | Onboarding and offline request demo |
| `9ec2019` | Public getter/transport/cache regressions and macOS smoke coverage |
