# Development and verification

Use an existing Python 3.11–3.13 environment; do not duplicate source archives or
large environments. Install `python -m pip install -e '.[dev]'` when necessary.

```sh
python -m pytest -q
ruff check src tests
mkdocs build --strict
python -m build
python -m twine check dist/*
git diff --check
```

Routine tests are offline and use small generated fixtures. Retained source replay
must forbid network and write only to scratch. Compare all keys and values for
selected first/leap/boundary/final dates; never overwrite approved Data for tests.
Full archive rebuilds are not necessary for cleanup validation. Wheel checks run
outside the checkout and verify removed modules/dependencies are absent as well
as running the retained tests. CI covers Python 3.11–3.13 and macOS.

Linux check names remain `tests (3.11)`, `tests (3.12)` and `tests (3.13)` to
match the existing required checks on `main`; the installed-package check is
`wheel`. The macOS check keeps its distinct `tests (macos-15, 3.12)` name.
As checked on 2026-10-21, required checks also require the PR branch to include
the current `main`. Keep these names stable when changing the matrix. This
workflow naming correction does not change branch protections or test scope.

The separate `main-merge-access` ruleset permits PR merges through the
`main-merge-stevetylda` team. On 2026-10-21 the user approved that team's
repository write assignment. It currently contains only `stevetylda`; future
members inherit its write access. Required checks and safeguards are unchanged.

A reduced test count after cleanup reflects removal of tests for retired products;
keep coverage of scientific reductions, UTC boundaries, missingness, membership,
credentials, cache integrity, rollback and interrupted publication recovery.
