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

## Navigation

Use scoped source search for known paths. The existing Graphify index predates
this cleanup and is not authoritative; do not rebuild merely for a query.
No local skill is required for ordinary pipeline edits.
