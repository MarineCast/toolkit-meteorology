# Live HRRR week: measured results

Executed on **2026-10-04**. [Run this example](live-week-demo.md).
The locally retained machine-readable measurement record contains checksums,
dependency versions, bounds and validation scope. Generated measurement artifacts
and data products remain local; the documented command rebuilds them from the public archive.

Period: **2024-01-01–2024-01-07**, America/Los_Angeles. **450 H3 R5 cells**, **75,600 hourly rows**. All seven daily releases and both summary products passed validation.

Source: NOAA/NCEP HRRR sfc/f00 analyses; nearest native-grid samples. These are retrospective environmental conditions, not observed species presence or as-issued weather forecasts.

## Resources

| Measurement | Result |
| --- | ---: |
| Elapsed, all recorded invocations | 1186.0 s |
| Peak process RSS | 965.5 MiB |
| HTTP requests reserved | 1,513 |
| Received response payload | 1.822 GiB |
| Reserved response payload | 1.831 GiB |
| Seven hourly Parquets | 2.942 MiB |
| both summary Parquet (32,472 rows) | 0.610 MiB |
| region summary Parquet (72 rows) | 0.031 MiB |
| frozen releases including evidence | 145.726 MiB |
| summary both | 0.633 MiB |
| summary region | 0.054 MiB |
| workspace including evidence | 279.725 MiB |
| Run metadata, excluding this Markdown report | 0.399 MiB |
| Total retained, excluding this Markdown report | 426.537 MiB |

Parquet sizes exclude manifests and source evidence; the storage categories include them. The summary directories add storage. No raw source retention policy was applied. GRIB messages cover the native CONUS grid before cropping. Payload accounting excludes HTTP/TLS overhead. Peak RSS includes decoder, validation and publication libraries.

## Daily regional means

Equal weight per H3 centroid-hour across the configured land-and-water box. These are sampled means, not area-weighted regional observations.

| Local date | Air temperature (°C) | Wind speed (m/s) | Cloud (%) | Visibility (km) |
| --- | ---: | ---: | ---: | ---: |
| 2024-01-01 | 5.00 | 3.11 | 65.63 | 15.83 |
| 2024-01-02 | 6.24 | 5.23 | 93.55 | 16.44 |
| 2024-01-03 | 6.62 | 4.00 | 84.94 | 14.02 |
| 2024-01-04 | 6.26 | 4.46 | 78.80 | 14.81 |
| 2024-01-05 | 5.59 | 5.45 | 92.71 | 14.00 |
| 2024-01-06 | 4.09 | 5.48 | 58.39 | 18.06 |
| 2024-01-07 | 2.48 | 2.76 | 66.94 | 17.93 |

## Spatial variation over the week

The H3 range below is the minimum–maximum of individual cells' **weekly means**, not the range of instantaneous weather. It describes spatial variation, not forecast error or uncertainty. A regional mean cannot reproduce local conditions when this range is wide; whether that matters for ecological forecasting needs evaluation.

| Metric | Unit | Regional weekly mean | H3 weekly mean minimum | H3 weekly mean maximum |
| --- | --- | ---: | ---: | ---: |
| MEAN_SEA_LEVEL_PRESSURE_HPA | hPa | 1016.332 | 1014.649 | 1018.025 |
| RELATIVE_HUMIDITY_2M_PCT | % | 82.097 | 65.691 | 90.358 |
| TEMPERATURE_2M_C | deg C | 5.182 | -5.761 | 9.759 |
| TOTAL_CLOUD_COVER_PCT | % | 77.278 | 60.982 | 94.048 |
| U_WIND_10M_MS | m/s | -0.068 | -2.488 | 3.234 |
| VISIBILITY_KM | km | 15.870 | 6.448 | 23.788 |
| V_WIND_10M_MS | m/s | 0.916 | -1.547 | 3.824 |
| WIND_GUST_SURFACE_MS | m/s | 7.207 | 2.082 | 12.808 |
| WIND_SPEED_10M_MS | m/s | 4.355 | 1.247 | 10.193 |

## Acceptance boundaries

- Implementation and real-source compatibility: passed for these cycles and bounds.
- Independent observational accuracy, multi-season regional acceptance and future forecast skill: **NOT_RUN**.
- Precipitation, astronomy and OrcaCast integration: outside this demo.
- Availability is an assumed fixed lag; retrieval time is recorded separately.

Exact checksums, invocation details, source manifests and counters are retained in `REPORT.json`, `TRANSFER.json` and the referenced daily/summary manifests.

## What this example establishes

The main acquisition/build/export invocation took **1,173.3 seconds (19.6 minutes)**.
The final installed-wheel reuse check took **12.1 seconds** and made **zero new
requests**. Its peak was 398.3 MiB; the main acquisition process peaked at 965.5 MiB.
The resource table includes the recorded main run and reuse time. These are local
measurements with concurrent development checks, not a controlled hardware benchmark.

A separate installed-wheel source check fetched one cycle using **9 requests and
11,418,213 payload bytes**, then repeated it without network access. Its normalized
Parquet matched the main pilot exactly. Those transfers and the initial connectivity
HEAD request are outside the main table. The main ledger includes one index-only
development retry; that request's 8,994 received bytes were not checkpointed, so the
received counter is a lower bound. Reserved bytes include the request.

Compared with the seven hourly Parquets, the combined summary is about **79% smaller**
and the region-only summary about **99% smaller**. These comparisons concern derived
Parquet files, not total retained storage. Approximately **426.5 MiB** remains when
workspace data, frozen provenance copies, summaries and run metadata are counted.

The whole-region weekly mean temperature is 5.18°C, but H3 weekly means range from
−5.76 to 9.76°C. Whole-region wind speed averages 4.36 m/s, while H3 weekly means
range from 1.25 to 10.19 m/s. Keeping both outputs preserves these spatial distinctions.
For marine forecasting, the usefulness of the land-and-water regional mean should be
compared with explicitly defined coastal/subregional summaries and independent
observations before choosing model inputs.

The next performance work should target full-grid decoding and retained-evidence
copies. Reducing H3 output resolution alone does not reduce the native GRIB transfer
or decoder footprint. A retention policy requires a separate decision about which
reproducibility evidence must remain; this demo deletes no published source objects.

## Implementation checks

- Full suite: **230 passed, 1 skipped** (the separate opt-in legacy daily HRRR/f01 test).
- Offline budget checks cover interruption, invalid responses/ranges, persistent limits,
  concurrent-writer rejection, DST planning and environment restoration.
- Regular wheel installation outside the checkout passed the real single-cycle and
  complete-week reuse checks using the existing dependency environment. Fresh dependency
  resolution was not tested.
- Ruff, catalog/policy freshness, strict documentation build and diff whitespace checks passed.

This evidence qualifies one retrospective week in the declared example box. It does
not qualify source eras, the area north of 49.70°N, station/buoy accuracy, species
forecast skill, or an as-issued future-weather product. The broader
[scientific validation plan](scientific-validation-plan.md) remains open.
