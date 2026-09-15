# Development

Install `.[test]`, then run `python -m pytest -q` and `git diff --check`.
Build with `python -m pip wheel --no-deps . --wheel-dir dist`, install the wheel into a separate
environment, and run `meteorology --workspace /tmp/weather-smoke init` outside the checkout.

`src/meteorology` owns producers, astronomy, inspections, manifests, schemas, and a local dataset
registry. `core` contains the minimal shared helpers extracted from OrcaCast; it has no application
imports. `maintenance` contains the catalog generator and optional live acquisition benchmark.

Configuration templates live in `config/`; their packaged copies in `src/meteorology/resources/config/`
must be synchronized when changed (including `config/data/`, which is tracked explicitly).
Run `meteorology catalog` and `meteorology feature-policy` after producer-schema changes. Catalog
materialization claims describe the selected workspace, not a universal release state.

`meteorology verify` checks an existing weather rebuild. `meteorology migrate-legacy` defaults to
planning; `--execute` archives configured legacy artifacts only after replacement validation.
No migration or provider benchmark is run by package installation or tests.
