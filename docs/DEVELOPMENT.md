# Development

Install `.[dev]`, then run `python -m pytest -q`, `ruff check src tests`,
`meteorology catalog --check`, `meteorology feature-policy --check`,
`mkdocs build --strict` and `git diff --check`. Build with `python -m build`
and check both distributions with `python -m twine check dist/*`. Install the
wheel into a fresh environment outside the checkout, run `meteorology --help`,
`meteorology --workspace /tmp/weather-smoke init`, `example-offline`, `validate`,
and `python -m pip check` there. See the [release checklist](development/release-process.md).

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

An optional live provider smoke is available after installing `.[acquisition]`:
`METEOROLOGY_LIVE_HRRR=1 python -m pytest -m live -q`. It fetches one fixed
2024 UTC cycle of f00 core fields and its matched f01 precipitation field for
a small Washington bounding box. Herbie may transfer full selected GRIB
messages before cropping; budget network/time accordingly. The test uses
temporary storage, requires live NOAA/Herbie access, and is skipped by normal
CI. Passing it establishes field decoding for that cycle, not regional parity.

GitHub Actions use reviewed major-version action tags. Review and update those
tags and Python dependency compatibility during each software release; the
release job accepts only an exact `vX.Y.Z` tag matching the canonical version.
PyPI publishing additionally requires the explicit repository variable and
protected `pypi` environment described in the release checklist.
