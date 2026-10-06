# CDS lifecycle integration and two separate pilot proposals

Status: proposed, no provider job submitted, no pilot authorized. Native source
qualification can precede the real marine mask; it does not qualify final H3
membership, coastal support, or the requested historical period.

## Implemented and testable offline

`meteorology.era5.jobs.run_job` requires an explicitly approved canonical request
identity and an injected bounded provider. The identity includes dataset, exact
request, study checksum and native-pilot purpose. State and budgets are persisted
under a single-writer lease. Known IDs resume without resubmission; interrupted
submission without a durable ID fails closed because CDS server-side exactly-once
submission is not established. No automatic provider deletion or cancellation.
An ambiguous job needs provider reconciliation before a distinct approved run;
there is intentionally no automatic attach or replay interface.

Polling has per-invocation time and count bounds, and aggregate resource accounting
survives resume. Every provider method reserves 64 KiB metadata transfer and one
operation before invocation. Downloads have a separate byte reservation, streamed
checksums, retained failed partials, and no overwrite/retry. Receipts contain only
public identities, safe job ID, state, byte count, checksum and resource counters.
No credentials, provider bodies or result URLs are stored. A completed download is
explicitly not scientific qualification; GRIB magic alone is not source validation.
Existing strict ERA5 decoder, expver consolidation and independent daily checks
remain required. Existing production study/mask gates remain in place.

Important boundary: method-level reservations do not bound hidden SDK HTTP calls.
No live adapter is enabled in this change. Before any pilot, implement and review
an HTTP transport enforcing per-response streaming limits, aggregate requests and
bytes, connect/read deadlines, no hidden retries/redirects, official CDS endpoint
ownership, approved storage host ownership and no token forwarding. Use official
non-waiting submission and job lookup, never convenience wait/download methods.
Do not enable cleanup or licence acceptance. Metadata/results errors must be
redacted at the application boundary as well as in receipts. Provider exceptions
are propagated by this low-level runner; callers must not log their text.

## ERA5 proposal: one UTC day, nine distribution points

Proposed day: 2024-01-01 UTC. This is a source-format pilot, not historical coverage
qualification. Exact NWSE area: [49.5, -125.5, 49.0, -125.0]; grid [0.25, 0.25].
This 3-by-3 grid lies inside the acquisition envelope. It is not a marine mask,
full-domain stencil, or resolution claim. Exact request A: single-level reanalysis,
seven variables (t2m, d2m, u10, v10, msl, tcc, tp), all 24 hours of January 1,
unarchived GRIB. Request B: only tp, January 2 at 00:00 UTC, same grid. Thus 169
single-expver messages / 25 unique valid times; precipitation uses January 1
01:00 through January 2 00:00. GRIB actually returned must independently verify
those slots, units, intervals, coordinate grid and expver; no missing-slot filling.

Proposed aggregate caps for two jobs in a fresh worker:

| Resource | Proposal |
|---|---:|
| Result download reservations | 8 MiB/job, 16 MiB total |
| API metadata reservation | 64 KiB/operation |
| Aggregate transfer | 20 MiB, including reserved API metadata |
| Provider operations | 64 maximum; underlying HTTP must also be bounded |
| Owned staging | 64 MiB, including partials, normalized rows and receipts |
| Cooperative RSS cap | 512 MiB |
| Poll window | 10 minutes/job, at most 20 polls/job, 30 seconds apart |
| Aggregate worker elapsed | 45 minutes including polling/download/processing |
| Initial processing target | 10 minutes, inside elapsed cap |
| Concurrency | 1 worker |

The float64 values-only lower bound is 9 * 169 * 8 = 12,168 bytes. This is not a
compressed transfer estimate. The existing conservative model permits two expvers:
9 * 338 * 2,048 = 6,230,016 bytes normalized allowance. With source reservation
16 MiB the model working set is 192,692,224 bytes, below the 512 MiB proposal.
The staging cap leaves room for source, normalization and receipts. Scientific
libraries in the dedicated environment occupy about 638 MiB separately; do not
count an existing environment as newly created weather staging. RSS and elapsed
checks are cooperative, not operating-system preemption. Real job queue time,
compressed size and delivered metadata remain unknown until an approved pilot.
If a cap is hit, retain evidence and ask for a revised budget; never widen silently.

## HRRR proposal: independent matching-day native source pilot

Use the same 2024-01-01 UTC and small coordinate region, with the existing native
sampling halo, separately pinned HRRR source/code identity. HRRR is a different
model, not a gap filler for ERA5. The eight existing f00 core parameters require
24 hourly cycles. Geographic cropping is local after national selected-field
range transfers; a small bbox does not make nationwide GRIB messages small.
The earlier cached-domain observation was approximately 266.5 MiB/day for core
fields, not a measurement of this exact day or a guaranteed estimate.

Propose 512 MiB aggregate transfer, 1 GiB owned staging, 1 GiB cooperative RSS,
300 HTTP requests, one worker, 45 minutes acquisition plus 15 minutes initial
compute (aggregate 60 minutes). Reserve one index and eight field requests per
hour: 216 core operations. An optional *separately reviewed* precipitation arm
would add 24 f01 index/one-field pairs (48 operations, total 264), conditional on
verified one-hour forecast accumulation metadata and selected-field size fitting
the remaining transfer budget. Otherwise the eight-core pilot must label
precipitation unqualified/unavailable. No guessed field keys, f00 zero rain, or
cumulative deaccumulation substitutions. Qualification must verify forecast
intervals before constructing the UTC 00:00–24:00 precipitation day.

Before authorization, refresh bounded read-only indexes to determine exact range
sizes and version/interval metadata for this day; stop if observed totals exceed
caps. This preparatory provider access itself needs an authorized bounded plan.
No NOAA request has been made for this proposal. Preserve shared cached inputs.

## Review and publication gates

Existing local auth setup is unchanged; PAT authentication was verified separately.
Dataset terms have not been proven by that check and must never be auto-accepted.
Review the transport adapter and this estimate before approving exact identities.
Use fresh owned scratch directories, then perform hourly/daily independent checks,
finite/missing values, time duplication, precipitation windows, source checksums,
code/dependency pins and resource receipts. No MarineCast/Data release until the
qualified real marine mask/registry and independently reviewed scientific outputs
exist. US water closure and the final registry remain unresolved upstream.

ERA5 can potentially cover the 2009 planning assumption, but no 2009 data was
acquired or qualified here. HRRR operation starts in 2014; pre-HRRR source options
remain ERA5 or an independently implemented/qualified ECCC source. Do not infer
historical completeness from either one-day pilot, erase cached files, blend
sources silently, or start a bulk acquisition from this proposal.
