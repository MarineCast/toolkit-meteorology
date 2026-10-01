# Changelog

All notable changes to this package are recorded here. Software versions follow semantic versioning; scientific method versions and data release IDs are independent.

## Unreleased

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
