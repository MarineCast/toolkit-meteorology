# ERA5 backfill performance and qualification

The monthly executor removes thousands of redundant jobs and substantially
reduces local processing. CDS authentication and existing licence acceptance were
verified on October 14, followed by a real two-day backfill and numerical checks.
The under-48-hour decade goal remains conditional on sustained provider throughput.

## Live CDS checkpoint and RH support

The [access checkpoint](evidence/era5-cds-access-20261014.json) verifies account
authentication and existing `cc-by` revision 1 acceptance without changing terms.
The credential is local with owner-only permissions and is excluded from reports.
The [live evidence](evidence/era5-backfill-live-20261014.json) retains counters,
source/report checksums and numerical results.

The first real day exposed two finite T/Td pairs whose Bolton water-RH exceeded
the existing 100.01 percent support limit: maximum 100.0141401 percent, with
dewpoint 0.001953125 K above temperature. The cause has not been independently
established. Backfill v2 keeps the formula/domain and records unsupported slots
explicitly, with null affected daily RH, `INVALID_HOURS`, `INVALID_DERIVATION_BITS`
and `VALUE_STATUS=OUTSIDE_SUPPORTED_RH_DOMAIN`. Other metrics retain independent
support. Existing short getters keep their strict whole-request rejection.
The [ECMWF documentation](https://confluence.ecmwf.int/pages/viewpage.action?pageId=388500357)
describes near-surface RH as a derivation from temperature/dewpoint, usually with
respect to water; this checkpoint does not adopt a different saturation formula.

The real 2024-01-01/02 optimized native run completed in **87.24 seconds** with
**230,653,952 bytes** sampled peak RSS (220 MiB), two source jobs/decodes, **23 HTTP
requests**, and **720,279 response bytes** including metadata. Raw GRIB totalled
**667,260 bytes**; retained cache plus output was **1,432,040 bytes** for 21,252 rows.
Summed concurrent stages were submission 2.71 s, client-observed queue/job wait
144.52 s, response transfer 1.90 s, decode/normalize 5.64 s, aggregation 2.29 s and
Parquet writing 0.108 s. Queue durations overlap and exceed wall time when summed.

The resumed daily-strategy reference, explicitly excluding RH, completed in
181.58 s with four decodes and 12,144 rows. Its earlier failed RH attempt and
diagnostic normalization remain charged/preserved in separate journals; two
initial source files were reused. This is an operational comparison, not a
controlled cold-run speedup ratio. All **12,144 common non-RH values** matched
within floating-point precision (maximum difference `4.55e-13`). Direct hourly
Bolton recomputation checked all 1,518 daily RH rows: 1,512 supported rows matched,
and six rows correctly stayed null, each with one unsupported hour. Independent
raw-GRIB reductions also checked all 7,590 added extrema and mean-vector direction
values (maximum difference `5.69e-14`). All 21,252 output rows were checked by
these calculations. No clipping or filled daily mean was used.

The real full-month run completed in **564.68 seconds** (9.41 minutes), with
**323,616,768 bytes** sampled peak RAM (309 MiB), two jobs/decodes, **43 HTTP
requests** and **10,368,880 response bytes**. It emitted **329,406 rows** and retained
**21,376,678 bytes** of cache plus output. Stage times were submission 2.47 s,
client-observed queue/job wait 488.30 s, transfer 3.09 s, decode/normalization
43.18 s, aggregation 19.10 s, and Parquet writes 1.32 s. It used one worker and
ran alongside the other benchmarks; these timings do not isolate server queue
time from provider production/status latency.

A fresh ARCO temperature probe retained a small regional sample for comparison.
All **36,432 hourly coordinate/time pairs** matched the CDS support exactly.
CDS-minus-ARCO temperature RMSE was **0.000462 K**, maximum absolute difference
**0.0010071 K**. This is temperature-only numerical evidence with no adopted
backend-equivalence threshold; precipitation, snowfall, other variables and
revision/forecast provenance remain separate qualification gates. The optional
`--sample-output` on `benchmarks/era5_arco.py --run` retains only the cropped
regional arrays, not global source planes.

The larger live runs use explicit 512-request, 512 MiB reserved-transfer and
30-minute limits. The month uses one worker and three-month run uses two;
they can compete in the CDS queue and share underlying provider caches. Their
results are measurements for those runs, not isolated throughput guarantees.

The real three-month run (January–March 2024, including leap day) completed in
**997.66 seconds** (16.63 minutes), with **966,966 rows**, four jobs/decodes,
**128 HTTP requests**, **30,400,231 response bytes**, and **328,761,344 bytes**
sampled peak RAM (314 MiB). Cache plus output totalled **62,705,148 bytes**.
Summed stages were submission 10.52 s, client-observed queue/job wait 1,621.44 s,
transfer 5.24 s, decode/normalization 170.45 s, aggregation 63.02 s and Parquet
writes 5.53 s. The two workers overlap those durations. Final manifests report
`PARTIAL` where RH support is incomplete, although all requested source chunks
and output partitions finished. A verified cached repeat returned 966,966 rows
in 4.84 s with **zero provider requests, transfers and GRIB decodes**.

At that observed two-worker throughput, a linear 3,652-day projection is
**11.12 hours**. Use **12–24 hours as a provisional planning range**, with the
48-hour target still conditional: queue load, provider caches, outages, retries
and local contention can change it. The one-worker month is consistent with
roughly 18.5 hours when extrapolated serially, or about 9.2 hours under ideal
two-worker overlap. Those are estimates, not a completed decade benchmark.

## Further local aggregation optimization

The reducer now reuses each day's hourly input arrays, packs hourly support flags
with NumPy, and serializes identical missing-input patterns once per selection.
The cache holds one day's inputs; it does not grow with the backfill interval.
The scientific method, schema, hourly bit ordering, missingness and lineage are
unchanged.

Two local replays of the retained real January source compared **all columns and
lineage for 329,406 rows exactly** against the previous reducer. Aggregation fell
from **12.06 s to 1.37 s** (8.83×) and **11.42 s to 1.14 s** (10.04×). These timings
exclude source decoding, network waits and Parquet publication. A linear decade
projection saves roughly 20 minutes of aggregation; it does not establish a
similar end-to-end speedup. The measured CDS wait remains the largest bottleneck.
See the [optimization evidence](evidence/era5-backfill-speed-20261014.json).

A bounded four-worker live attempt downloaded all three monthly sources and
committed January/February in 83.91 s, but stopped on the terminal boundary with
`AmbiguousSubmission`: no durable remote ID was returned. The read-only CDS job
list confirmed the three known monthly jobs; it did not resolve the uncertain
boundary submission. Raw evidence, both partitions and all charged counters were
retained. No ambiguous request was resubmitted. This is an **incomplete run**, not
a completed three-month timing or a controlled two-versus-four-worker comparison.
Provider cache state also differs from the earlier two-worker run. Four workers
remain configurable, but their sustained throughput is unqualified. Keep the
12–24-hour decade planning range until a complete comparison is available.

The local replay is reproducible from retained source evidence without provider
access. Export the previous reducer from revision `1532a30` to a local file, then:

```sh
python benchmarks/era5_aggregation.py \
  --source-record /path/to/source/SOURCE.json \
  --boundary-cube /path/to/following-month/edges/midnight.npz \
  --reference-module /tmp/backfill_compute_previous.py \
  --start 2024-01-01 --end 2024-01-31 \
  --bbox -129.7 45.9 -121.5 51.5 \
  --output-dir /tmp/aggregation-replay
```

The output directory must be new. Replay verifies source/normalized checksums,
uses the existing bounded decoder, and retains normalized scratch plus its small
report there. It makes zero network requests and does not modify source caches.

## Scope and reproducibility

Measurements were made on 2026-10-14, macOS, Python 3.12.13 and ecCodes 2.47.1,
with bbox `[-129.7, 45.9, -121.5, 51.5]`: 936 halo grid points and 759 emitted
native points. Primary variables require seven source parameters and produce 14
daily metrics. Optional snowfall was not part of these performance runs.

The default benchmark uses actual generated GRIB through the normal source/job/
normalization/Parquet path and mocked transport. Fixture fields are constant and
compress unusually well. Their payload size and mock queue time cannot estimate
live CDS transfer, meteorological variability or scientific accuracy. Baseline
and optimized runs use separate cache namespaces. Existing baseline getters are
invoked in consecutive seven-day requests; the optimized API plans the whole
period. The two background baseline comparisons also compete for local CPU.

```sh
python benchmarks/era5_backfill.py --period two-day --output-dir /tmp/backfill-two-day
python benchmarks/era5_backfill.py --period month --output-dir /tmp/backfill-month
python benchmarks/era5_backfill.py --period three-month --only optimized \
  --output-dir /tmp/backfill-three-month
```

Use a fresh output/cache for a cold run. `--live` explicitly performs CDS access
after credential and manual-terms setup; run the two-day pilot before expanding.
`--only optimized` in a fresh process measures pipeline peak RSS without the old
getter's allocator or the parity dictionary. Paired comparison verifies every
common date/grid/metric value with `rtol=1e-10`, `atol=1e-8`, including NaN parity.
Metrics added by the backfill are checked separately by regression tests.
Compact reports and actual ARCO object checksums are retained in
the [machine-readable evidence report](evidence/era5-backfill-20261014.json).

## Generated GRIB comparison

The original `b59567f` reducer did not finish its first full-area day after more
than 20 minutes at 100% CPU. It was interrupted with its source, normalized data
and journals preserved. Profiling identified repeated pandas deep copies of the
forecast-support catalog during scalar lineage lookups. The reducer now clears
attrs on a shallow local copy while preserving the caller's catalog and numbers.
The comparisons below include that shared CPU fix in both workflows; they are
not timings of the untouched old revision.

| Full-area two-day measurement | Daily strategy with CPU fix | Monthly backfill |
| --- | ---: | ---: |
| End-to-end wall time including parity projection | 76.07 s | 2.21 s |
| Source jobs / GRIB decodes | 4 / 4 | 2 / 2 |
| Mock provider operations | 18 | 10 |
| Startup cache walks | 1 | 3 |
| Source payload bytes | 36,504 | 36,396 |
| Retained cache bytes | 31,478,866 | 58,478 |
| Daily rows (9 versus 14 metrics) | 13,662 | 21,252 |

All 13,662 common values matched exactly (maximum absolute difference zero).
The optimized output directory is counted separately in the machine report;
cache-only size is not total storage. Its sampled final RSS in the paired process
was roughly 769 MB after the baseline, versus 760 MB after baseline projection.
Those are warmed process snapshots, not a valid peak-memory comparison.

The fresh-process three-month optimized run covered 2024-01-01 through
2024-03-31, including leap day. It completed the pipeline in **135.34 s**
(139.19 s including projection), wrote **966,966 rows**, used four source jobs/
decodes and 18 mock provider operations, and retained **27,755,483 bytes** across
cache and output. Peak pipeline RSS sampled every 20 ms was **451,887,104 bytes**
(431 MiB), within the 1 GiB cap. Projection raised RSS afterward and is excluded
from that pipeline peak.

| Three-month optimized stage | Summed worker/main time |
| --- | ---: |
| Mock submission including fixture construction | 46.39 s |
| Client-observed mock queue/job wait | 0.10 s |
| Mock response transfer | 0.002 s |
| GRIB decode and numeric normalization | 91.01 s |
| Native spatial selection | 0.0002 s |
| Temporal aggregation and row construction | 43.83 s |
| Parquet write calls | 3.36 s |

Concurrent stage durations overlap and must not be added to derive wall time.
Client-observed queue/job time includes polling and status-call latency, not
server-exclusive queue telemetry. Validation/checksumming, journal writes and
publication are included in total pipeline time. Counters in the result distinguish
current invocation from original historical receipts; cache hits report zero
current provider work.

A fresh-process **full-month optimized** run completed January 2024 in **85.49 s**
(86.90 s including projection), with **329,406 rows**, two jobs/decodes, ten mock
operations, **326,926,336 bytes** sampled peak RSS (312 MiB), and **9,462,767 bytes**
retained cache plus output. Stage times were submission/fixture construction
11.03 s, client-observed mock wait 0.096 s, transfer 0.0006 s, decode/normalization
53.63 s, spatial selection 0.0001 s, aggregation 15.77 s and Parquet writes 1.20 s.
It ran alongside other CPU work; its wall time is not directly comparable to
an otherwise idle three-month projection.

A repeat against committed implementation `3e7eabc` completed the three-month
pipeline in **176.97 s** (180.98 s including projection), with identical row/job/
decode counts, **425,246,720 bytes** sampled peak pipeline RSS (406 MiB), and
**27,755,667 bytes** cache plus output. Submission/fixture construction took
66.22 s, client-observed mock wait 0.299 s, transfer 0.009 s, decode/normalization
140.17 s, native spatial selection 0.0002 s, aggregation 46.50 s and Parquet
writes 3.44 s. Both three-month runs competed with baseline/test processes;
the spread illustrates why timing is descriptive rather than a CI threshold.

The full-area daily-strategy month run was interrupted after **more than 27
minutes**, and its three-month counterpart after **more than 20 minutes**.
They had committed the January 1–28 and January 1–14 prefixes respectively; these
are bounded lower bounds, not completed full-period timings. Retained sources,
getter manifests, counters and interruption logs were preserved. A separate
comparison of the completed 21-day baseline prefix against the optimized full
month matched **143,451 common values exactly**. The full old month/three-month
comparison remains incomplete. Later live CDS evidence is recorded above.

## Real public ARCO probe

The inspected [Google ARCO ERA5 collection](https://github.com/google-research/arco-era5)
was `ar/full_37-1h-0p25deg-chunk-1.zarr-v3`. Live consolidated metadata had SHA256
`2a414847a800690c34146b48e40ba50da3218a7e4b2da4be9d5d82e5c607d81f`.
All eight relevant source variables were present. Despite the object suffix,
the examined arrays declared Zarr format 2, float32, Blosc/LZ4 and chunks
`[1, 721, 1440]`: a global spatial plane per UTC hour. This is a poor geographic
match for a 759-point regional request. No GRIB message-level init/step/expver
evidence was present in the examined arrays.

```sh
python -m pip install numcodecs  # optional benchmark only, not a runtime dependency
python benchmarks/era5_arco.py --run \
  --output /tmp/arco-two-day-temperature.json
```

For real temperature on 2024-01-01/02, the bounded probe made **52 GETs**, transferred
**112,734,309 bytes** in **30.21 s**, and sampled peak RSS **113,360,896 bytes**.
Decode/crop took **0.161 s**. Actual time coordinates were checked and cropped
regional shape was `48 × 23 × 33`; temperatures ranged 261.310–285.516 K.
The optional codec was installed outside the project; it is not required for CDS.

The original probe was temperature-only transfer/representation evidence. The
later checkpoint above adds temperature numerical comparison with CDS, without
establishing complete meteorological equivalence. Month/three-month ARCO all-variable
transfers were not widened beyond the bounded probe because the global chunks
already establish the overfetch issue. Other ARCO collections/layouts were not
exhaustively tested. CDS remains the implemented strict regional backend; the
fastest scientifically equivalent backend has **not** been established by a live
head-to-head comparison. ARCO is deliberately unavailable as an execution backend.

Live metadata was also checked for `co/single-level-reanalysis.zarr-v2` (181,026
bytes) and `co/single-level-forecast.zarr-v2` (97,308 bytes). Their meteorological
chunks cover all 542,080 reduced-Gaussian grid values: `[1, 542080]` for
instantaneous fields and `[1, 1, 542080]` for forecast-step accumulations. These
stores expose a different spatial/forecast representation, with no regional
spatial chunks. Their contents were not substituted for validated CDS GRIB.
Exact metadata hashes, variable arrays and collection vintages are in the report.

## Conditional ten-year estimate

The full-area 2009-01-01–2018-12-31 plan has 3,652 days, **121 source jobs**
(120 months plus one terminal hour), compared with **7,304** daily-strategy jobs.
The conservative monthly numeric cube is at most 38,997,504 bytes for the default
seven parameters. Default reserved source transfer is at most roughly 7.6 GiB
before retries/metadata; this is a reservation bound, not measured payload size.

The live throughput projection above supersedes the earlier provider-free estimate
below. It does not establish stationary CDS queue behavior.

Linear projection of the generated three-month pipelines gives about **1.5–2 hours**
for 3,652 days of local software work. Treat **three hours** as a planning allowance,
not an empirical real-GRIB guarantee: entropy, source revisions, disk, machine,
content validation and interference can change processing cost. Live provider
queue/production and actual regional transfer dominate the remaining uncertainty.
With two sustained independent workers, a simple planning model is:

`total ≈ local processing + 61 × mean monthly queue/production time + transfer/retries`

| Assumed sustained queue/production per monthly job | Contribution at two workers |
| --- | ---: |
| 10 minutes | about 10.2 hours |
| 30 minutes | about 30.5 hours |
| 60 minutes | about 61 hours |

The model assumes CDS permits two concurrent jobs and neither shared throttling
nor outages serialize them. Three hours local plus roughly 45 hours provider/
transfer overhead could meet the target; that requires average monthly throughput
better than roughly 44 minutes per job with adequate transfer margin. None of
these queue scenarios has been measured here. The
[CDS documentation](https://confluence.ecmwf.int/spaces/CKB/pages/174856258/Climate%2BData%2BStore%2BCDS%2Bdocumentation)
describes dynamic limits/queues; job count reduction does not guarantee a
particular queue time.

## Qualification gates and recommended settings

Use daily native output, two workers, the default 1 GiB RAM/2 GiB staging and
64 MiB source cap initially. Keep raw GRIB, partition companions and manifests;
retain hourly scratch only when needed. Set an explicit retained-cache quota
based on the measured pilot, with output disk provisioned separately. Do not
claim increased resolution for H3 or infer ERA5 visibility/fog.

Generated regressions cover monthly/leap boundaries, accumulation units,
mean hourly wind magnitude, calm direction, source revisions, incomplete hours,
checksums, interrupted publication/resume, selective repair, same-source duplicate
callers, independent acquisition, size splitting and persistent original caps.
They establish software behavior and calculation parity, not real regional accuracy.

Live CDS authentication and two-day/month/three-month results are recorded above.
CDS-versus-ARCO all-variable
accumulation/revision equivalence, independent regional meteorological accuracy,
shared marine-mask qualification, actual decade throughput and a production
MarineCast ingest remain **NOT_RUN** or unestablished.
The implementation never promotes synthetic evidence into those qualifications.
