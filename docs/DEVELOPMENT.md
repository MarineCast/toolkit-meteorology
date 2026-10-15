# Development

Historical backfill regressions: `python -m pytest -q tests/test_backfill.py`.
`python benchmarks/era5_backfill.py --period two-day --bbox -125.25 49 -125 49.25`
is a small deterministic generated-GRIB comparison. Full-area/monthly runs are
manual performance evidence, without CI timing thresholds. Add `--only optimized`
in a fresh process for isolated pipeline peak RAM. Live CDS and public ARCO probe
commands, scope and unrun gates are in the
[backfill performance report](era5-backfill-performance.md).

Install `.[dev,native-pilot]`, then run `python -m pytest -q`,
`ruff check src tests benchmarks demo`,
`meteorology catalog --check`, `meteorology feature-policy --check`,
`mkdocs build --strict` and `git diff --check`. Build with `python -m build`
and check both distributions with `python -m twine check dist/*`. Install the
wheel into a fresh environment outside the checkout, run `meteorology --help`,
`meteorology --workspace /tmp/weather-smoke init`, `example-offline`, `validate`,
and `python -m pip check` there. Also exercise `meteorology sources`,
`meteorology variables --data-source era5` and a `meteorology get ... --dry-run`
outside the checkout; the core install must not require Folium, Branca or
GeoPandas. See the [release checklist](development/release-process.md).

Generated native GRIB fixtures require ecCodes from `.[native-pilot]` or `.[era5]`.
Without that extra the codec-dependent checks skip; normal tests never contact
NOAA/CDS. For focused request checks, run:

```sh
python -m pytest -q tests/test_request_api.py tests/test_request_transport.py \
  tests/test_processing_scaling.py tests/test_concurrency.py
python demo/request_offline.py --output /tmp/request-demo
python benchmarks/native_processing.py /tmp/request-native-processing.json
```

The request demo requires `.[era5]` and a fresh output directory. Benchmarks use
synthetic inputs and record deterministic read/decode counts separately from
environment-dependent runtime and memory. See the
[measured evidence and tradeoffs](request-refactor-evidence.md).

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

Request review regressions: `python -m pytest -q tests/test_request_review.py
tests/test_request_lineage.py tests/test_request_preflight.py
tests/test_request_storage.py`. These exercise real immutable caches, generated
GRIB and mocked provider revisions; they make no provider requests.
`python benchmarks/request_review.py after` reports deterministic operation
counts and descriptive wall time/memory. There are no timing thresholds.

For the default regional API, start with `python -m pytest -q tests/test_regional.py`.
These tests inject local source fixtures and never contact ECMWF. CI also copies
this test outside the checkout and runs it against the regular wheel installation.
The [cleanup inventory](request-cleanup.md) records deletion/retention decisions;
`benchmarks/README.md` in the repository indexes the optional benchmark scripts.
