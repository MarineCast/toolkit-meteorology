# toolkit-meteorology agent guidance

## Supported outcome

This repository supports one product: the approved coastal H3 R5 ERA5 hourly and
daily weather Parquets plus `catalog.json`. Keep the pipeline small. Do not restore
removed HRRR/CDS request planners, astronomy, modeling, visualization, per-family
entry points, or optional runtime stacks without explicit new product scope.
Legacy implementation remains in Git history; `docs/cleanup.md` and
`docs/MIGRATION.md` describe the removal.

Start with `git status --short`; preserve concurrent edits. Read
`docs/regional-api.md`, `docs/CONTRACTS.md`, and `docs/ARCHITECTURE.md` before
producer changes. Shared boundaries follow `../../.github/INFRASTRUCTURE.md` when
working in the grouped MarineCast workspace. The installed package must not
import siblings, application code, workspace scripts, or checkout-only files.

## Public API and configuration

`meteorology.get_weather(start_date=None, end_date=None, variable_list=None, **options)`
publishes inclusive UTC days. `None` selects configured METRICS, falling back to
`ALLOWED_VARIABLES`: temperature,
dewpoint, relative_humidity, wind, cloud_cover, pressure, precipitation. All seven
produce 14 daily and 10 hourly metrics. Reject unknown, duplicate, or empty
selections before source acquisition. `check_weather(catalog_path,
study_config=...)` is read-only; supply the approved study to verify external
membership. `validate_product` is a compatibility spelling for catalog checks.

`run_settings.py` resolves explicit arguments > process environment > `.env` in
CWD for DATA_DIR, START_DATE, END_DATE, VERBOSITY, METRICS and REBUILD. Dates can be omitted when
configured; file-relative output paths resolve beside that file. `env_file` /
`--env-file` selects a file explicitly. No implicit parent search or environment
mutation. Keep personal `.env` ignored; commit only `.env.example`. Progress is
stderr (`verbosity=True` / `--verbose`); explicit false / `--no-verbose` silences it.
`run_settings.preflight` is the single settings gate. CLI/file-backed runs require
all six effective values; no-file Python API calls retain validated optional defaults.
Check date order, unique allowed metrics, booleans, readable/unique assignments and
output ancestor permissions before study loading, locks or source access. Report
all errors together without raw values. `meteorology preflight` checks settings
without running. Test settings in temporary directories, never personal defaults. Verbose diagnostics
use `run_settings.log` on stderr with UTC timestamps: whitelist resolved run keys,
show actual missing/query plans, cache/API events and measured stage timings.
Credentials, headers, response bodies and raw exception text must not enter logs.
Do not claim automatic retries: none are implemented. Preserve quiet progress when
false and catalog-only CLI stdout; test cache/no-op and failure-redaction messages.

The installed CLI is `meteorology get [START] [END] --variables ...` and
`meteorology check CATALOG --study-config PATH`; `python -m meteorology` uses the
same implementation. Resolve the owner-managed study via explicit `study_config`
or `MARINECAST_STUDY_CONFIG`; registry/mask inputs are external, not bundled data.
Use explicit scratch `output_dir` and canonical retained `cache_dir` in checks.
Default REBUILD=False validates and preserves existing dates, querying only missing
dates (including bridges for disjoint ranges). A request with covered dates/metrics is a no-op.
REBUILD=True replaces the requested range/metrics. Both modes build absent outputs.
Append expands newly requested metrics across the full retained range, preserves
old columns (even if omitted) and aligns added values by cell/time keys. Record
metric-scoped source epochs when vintages differ; existing nulls are preserved. Validate
existing membership and provenance, keep source_epochs for combined vintages,
and hold the output transaction lock from inspection through publication. Tests
must cover both modes, no-op, prepend/tail/bridge ranges, full-range metric
backfill/key alignment, preflight side effects and rollback.
Resolve provider credentials through `credentials.py`; never expose tokens in
logs, exceptions, catalogs, fixtures, or committed configuration.

## Code and test map

| Responsibility | Implementation | Focused tests |
| --- | --- | --- |
| API, variable/date gates, wide tables, catalog validation, CLI | `regional.py`, package exports, `cli.py` | `test_weather_api.py`, `test_regional.py` |
| Fast ECMWF geographic chunks, bounded transfers/concurrency, pinned metadata and checksummed caches | `regional_source.py` | `test_regional.py` |
| Native sampling, unit conversion, hourly fields and UTC reductions | `compute.py` | `test_regional.py`, `test_weather_api.py` |
| Approved study and exact registry/mask membership | `study.py`, `marine_reporting.py`, `resources/` | `test_study.py`, `test_marine_reporting.py` |
| Local run defaults and precedence | `run_settings.py` | `test_run_settings.py`, `test_weather_api.py` |
| Credential resolution and redaction | `credentials.py` | `test_credentials.py` |
| Transactional publication/recovery and hashes | `core/artifacts/`, `artifacts.py` | `test_publication.py`, `test_regional.py` |
| Original retained multi-epoch product merger; no acquisition | `coastal.py` | `test_coastal.py` |

`regional.get_data` is an internal shared exporter retained by the implementation
and compatibility tests; it is not the supported root API. The retained merger
and workspace update helpers do not make additional products public interfaces.

## Scientific and storage constraints

Preserve exact approved membership (currently 463 cells), keys, units, UTC interval
semantics, null/status accounting, source vintages and provenance. Quarter-degree
nearest-source sampling does not create independent H3-scale weather information.
Instantaneous daily support is 00–23 UTC; precipitation uses intervals ending
01:00 through next midnight. Missing support stays null, including unsupported
RH. Daily wind direction uses the mean vector. Do not zero-fill, clip, or silently
substitute revised source values.

Discover coverage from actual source coordinates, not calendar lag. The explicit
`allow_incomplete_final_day` option permits one missing terminal precipitation
hour; it must not conceal larger gaps. ERA5/ERA5T revision identity and historical
operational availability remain unknown where evidence is absent. This is
retrospective reanalysis, not a qualified forecast archive. Source snapshots and
epoch lineage must remain explicit; fresh acquisition need not reproduce a prior
vintage. Reruns reuse completed verified objects; do not describe this as automatic
retry or portable full multi-epoch reconstruction without implementing/testing it.

Never modify approved `MarineCast/Data/Meteorology` or retained inputs/caches for
tests or source cleanup. Use small offline fixtures and bounded scratch replay;
no full historical rebuild, bulk copies, or duplicate large environments unless
explicitly scoped. Check storage headroom before acquisition/staging and run one
writer per output/cache. Preserve recoverable publication and source-cache integrity.

## Verification

Reuse an existing Python 3.11–3.13 environment (`.venv` locally when available).
Runtime dependencies are h3, numpy, numcodecs, pandas, pyarrow and PyYAML; developer
tools belong in optional extras. For behavior changes run focused tests, then:

```sh
python -m pytest -q
ruff check src tests
mkdocs build --strict
python -m build
python -m twine check dist/*
git diff --check
```

Follow `.github/workflows/tests.yml` for outside-checkout installed-wheel tests,
CLI smoke and `pip check`; ensure packaged resources resolve without the checkout.
Routine tests must not contact providers. Cover scientific reductions, next-midnight
and block boundaries, exact membership/keys, null/status totals, corrupted caches,
selected source dependencies, rollback and interrupted publication recovery when
affected. Docs/instruction-only edits need strict docs and `git diff --check`.
Report what actually ran and what did not: live acquisition, full reconstruction,
observational accuracy and other Python versions are separate claims. Historical
test counts and dated acceptance evidence in docs are not current verification.

## Documentation, generated files and navigation

Edit documentation in `docs/` and configuration in `mkdocs.yml`. `site/` is ignored
generated HTML from `mkdocs build`; do not edit or commit it. Reuse that destination.
Python bytecode (`*.py[cod]`) and `__pycache__/` are already ignored. Authorized
bytecode cleanup is local to the requested source/test trees; preserve environments,
data and retained source caches. Inspect hidden entries, do not follow symlinks,
and remove resulting ordinary empty directories with empty-only checks. Use
`PYTHONDONTWRITEBYTECODE=1` for post-cleanup import checks.

Use scoped `rg` for known paths and symbols. Optional Graphify navigation lives in
ignored `graphify-out/`; check the manifest and `refresh-metadata.json` against
relevant source. Source/tests outrank graph edges; code-only AST extraction omits
prose semantics and can miss dynamic references. The current refresh records
dangling endpoints and possible relation collapse, so no-match is not proof of absence.
Do not rebuild just to answer a query. For an explicitly requested refresh use the
Graphify skill and existing isolated tooling, scoped to this checkout, with
`graphify extract . --code-only --no-cluster`. After deletions, an in-place forced
refresh can preserve orphan nodes; use a fresh scratch output and verify paths and
diagnostics before replacing the local index. Keep graphs local/ignored; do not
install hooks, merge global graphs, publish caches, or overwrite maintained guidance
through generated Graphify integration. No local skill is required for ordinary
pipeline edits.
