# Changelog

## Unreleased — preflight and metric backfill

Add one side-effect-free settings preflight with aggregated errors, required file
settings, date/metric/boolean/path checks, and an offline `meteorology preflight`
command. Append can expand requested metric families across the entire retained
date range while preserving old columns, aligning exact keys and recording
metric-scoped lineage for overlapping vintages.

## Unreleased — run settings and incremental dates

Read ignored local `.env` run defaults, with a committed `.env.example`: output,
UTC dates, verbosity, requested metric families and rebuild mode. Explicit arguments
override environment/file settings. Append validates existing data, preserves
saved dates/columns/vintages, acquires missing dates and backfills new metrics
across the retained range; covered requests are no-ops.
Explicit rebuild replaces requested dates/metrics. Both create an absent product;
transactional rollback and source-epoch lineage apply to incremental publication.

## Unreleased — legacy removal

Remove retired HRRR/CDS request engines, astronomy, model features, visualizers,
demos/benchmarks, legacy configuration, tests and docs. Extract unchanged UTC
numerical reductions into compute.py; keep the fast source cache, exact membership,
current catalog checks, retained epoch merger and transaction recovery. Reduce
runtime dependencies to six. See [migration](MIGRATION.md) and [inventory](cleanup.md).


## Prior coastal API rollout — PR #15

- Add `get_weather(start_date, end_date, variable_list)` for exact approved coastal
  R5 output using the fast regional source cache; seven allowed families produce
  the current 14 daily and 10 hourly metrics.
- Add read-only `check_weather`, current-catalog dispatch in `validate_product`,
  and `python -m meteorology.regional get/check` commands.
- Rename the old five-argument public getter to `get_weather_request`; existing
  per-family getters continue to use that compatibility workflow.
- Verify full current structure and three retained-source days without altering
  approved data. See [verification and limits](coastal-product.md#primary-api-verification-2026-10-17).


The canonical [CHANGELOG.md](https://github.com/MarineCast/toolkit-meteorology/blob/main/CHANGELOG.md) is maintained in the repository root so package, source and release reviewers read the same version history.

The current 0.1.0 entry is a proposed initial release. A changelog entry alone does not establish that a GitHub Release or PyPI distribution has been published.
