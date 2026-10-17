# toolkit-meteorology agent guidance

## Scope

One supported product: exact approved coastal R5 ERA5 hourly/daily files and catalog.
Read docs/ARCHITECTURE.md, docs/CONTRACTS.md and docs/regional-api.md before producer
changes. Shared boundaries follow ../../.github/INFRASTRUCTURE.md in a grouped
workspace. No sibling runtime imports. Inspect Git state and preserve concurrent work.

## Scientific and storage constraints

Preserve exact registry/mask membership, units, UTC interval semantics, nulls,
source vintages and provenance. Unknown operational availability stays unknown;
retrospective ERA5/ERA5T is not a forecast qualification. Never modify approved
Data or retained source caches for tests. Use small offline fixtures and bounded
scratch replay; no bulk copies or duplicate environments. User-requested source
cleanup does not authorize data deletion. Retained legacy source is in Git history.

## Checks

Run focused tests, then `python -m pytest -q`, `ruff check src tests`,
`mkdocs build --strict`, wheel/sdist build and outside-checkout installed-wheel tests.
Ordinary tests must not access providers. Docs-only edits require strict docs and
`git diff --check`. Test cache corruption, incomplete rainfall boundaries, scientific
reductions, exact keys/membership and publication failure/recovery when affected.
Report exactly what ran, including untested live acquisition or full reconstruction.

## Documentation and generated files

Edit and commit documentation sources in `docs/` and configuration in `mkdocs.yml`.
`site/` is generated HTML from `mkdocs build`; it is disposable, ignored, and must
not be edited as documentation source or committed. Reuse that build destination.

Python bytecode (`*.py[cod]`) and `__pycache__/` are generated and already ignored.
Do not commit them. For an authorized local bytecode cleanup, limit removal to the
requested repository source/test trees, preserve environments and retained data or
source caches, and remove resulting ordinary empty directories with empty-only
checks. Inspect hidden entries before calling a directory empty; do not follow
symlinks. Use `PYTHONDONTWRITEBYTECODE=1` for import checks after bytecode cleanup.

## Navigation

Use scoped source search for known paths. The optional local Graphify index lives
in ignored `graphify-out/`; source and tests remain authoritative. Check its
manifest and `refresh-metadata.json` against relevant source before querying. Read
recorded diagnostic limitations; missing edges do not prove absence. Code-only
AST extraction omits documentation semantics and may miss dynamic relationships.
Do not rebuild merely for a query. When a refresh is explicitly requested, reuse
the existing isolated Graphify installation and run from this repository only:
`graphify extract . --code-only --no-cluster`. After intentional source deletions,
verify the deleted paths before using `--force` to replace a larger stale graph.
Keep graphs local and ignored; do not enable hooks, global merging, or publishing.
No local skill is required for ordinary pipeline edits; use the Graphify skill
when refreshing or exporting its index.
