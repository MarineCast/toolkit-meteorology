# ERA5 backfill performance and qualification

The monthly executor removes thousands of redundant jobs and substantially
reduces local processing. Actual CDS queue/transfer throughput is still unmeasured
because this environment has no CDS credentials or accepted-term evidence.
The under-48-hour decade goal remains conditional.

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
comparison remains incomplete; live CDS runs for all three periods remain unrun.

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

This is temperature-only transfer/representation evidence, not numerical parity
with CDS or complete meteorological validation. Month/three-month ARCO all-variable
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

Live CDS two-day, month and three-month benchmarks remain **NOT_RUN** pending
account/token/manual terms. CDS-versus-ARCO numerical/accumulation/revision parity,
independent regional meteorological accuracy, shared marine-mask qualification,
actual decade throughput and a production MarineCast ingest remain **NOT_RUN**.
The implementation never promotes synthetic evidence into those qualifications.
