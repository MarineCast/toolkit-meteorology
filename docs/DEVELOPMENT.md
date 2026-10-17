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

A reduced test count after cleanup reflects removal of tests for retired products;
keep coverage of scientific reductions, UTC boundaries, missingness, membership,
credentials, cache integrity, rollback and interrupted publication recovery.
