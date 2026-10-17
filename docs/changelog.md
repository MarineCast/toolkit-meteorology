# Changelog

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
