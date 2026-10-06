# Changelog

All notable changes to this package are recorded here. Software versions follow semantic versioning; scientific method versions and data release IDs are independent.

## Unreleased

- Added `demo-hourly-week`: bounded, resumable direct NOAA AWS byte-range acquisition,
  the existing strict decoded-grid and hourly release pipeline, native-H3 and regional
  summaries, persistent transfer accounting and readable resource/spatial-variation reports.
  The documentation includes a reproducible one-week example; empirical forecast skill
  remains a separate acceptance gate.

- Added daily/weekly exports of validated hourly atmosphere with native-H3 and regional
  statistics, explicit coverage/as-of filtering, streaming day-at-a-time processing, Zstandard
  compression and checksum-backed publication. These are retrospective context, not forecasts.
- Decoupled hourly normalized inputs from unused precipitation while keeping legacy bundles readable.
- Rejected precipitation step/valid-time metadata that contradicts interval timestamps.

- Removed unused inherited stage/artifact classes, H3 utilities and aliases, weather wrappers,
  and area-range configuration helpers. Direct imports of those removed internals are no longer
  supported; public producer APIs, schemas, scientific calculations and stored products are unchanged.
- Added strict release IDs, method-version metadata, read-only product validation and explicit release freezing.
- Added an installed-wheel, offline synthetic example and bounded HRRR download confirmation.
- Added vector mean wind components/speed/direction to daily weather; calm direction is null (`hrrr_surface_daily_v2`).
- Defined polar-night solar elevation and no-night lunar fractions as null, and corrected zero-range daylight normalization (`daylight_astronomy_v2`, `lunar_illumination_v2`).
- Added the schema-backed variable inventory, public API, MkDocs site and release CI.

## 0.1.0 — proposed initial production release

- Independently installable `meteorology` package and CLI for R4/R5/R6 support, retrospective HRRR R5 weather, R4 daylight, R5 lunar context and native-resolution daily matrix.
- HRRR core uses f00 analyses; precipitation uses matched f01 rates and is explicitly a six-snapshot estimate rather than a complete 24-hour accumulation.
- Scientific gaps: no future-weather forecast validation, no live-provider certification from offline fixtures, no global/per-cell timezone or area-weighted H3 support.
- The source migration from OrcaCast is historical; downstream application integration and large regional release checks remain separate.

No GitHub, Pages or PyPI release is implied by this changelog entry.

## Local review candidate — shared study v1

- Add explicit shared-study preflight/config selection with portable Data-root resolution, canonical identity, production approval/registry gates, and source-derived summary provenance. Standalone workspace configuration remains available. No provider adapter, marine mask, acquisition or publication is added.
