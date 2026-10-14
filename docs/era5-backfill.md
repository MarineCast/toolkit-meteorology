# ERA5 historical backfill

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
