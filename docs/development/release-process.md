# Software and data release process

## Three independent identities

1. **Software version** follows semantic versioning and comes from `src/meteorology/_version.py`, which also drives wheel metadata and `meteorology.__version__`.
2. **Scientific method version** comes from `meteorology.methods.METHOD_VERSIONS`. Bump the affected method when numerical meaning or missingness changes, even if the output column name stays the same.
3. **Data release ID** is a content-derived hash of product, software and method versions, resolved configuration and declared input/output checksums. `run_id` identifies one execution; a repeated execution can have the same release ID if the declared content is identical. Configuration paths participate in the hash when present.

Working family outputs are mutable. Before replacing one that must be retained, run `meteorology freeze-release --manifest ... --output-root ...`. The command validates inputs and artifacts, copies them to a new release-ID directory, verifies copied checksums, and refuses a pre-existing target. Weather freezes also copy inventory-addressed compact samples and crosswalks. Frozen release checksums detect later mutation; the filesystem owner still controls retention and permissions.

## Release checklist

- [ ] Review scientific changes and bump all affected method IDs; update [methodology](../methodology.md), [variables](../reference/variables.md), schema and migration notes.
- [ ] Update `src/meteorology/_version.py` and [CHANGELOG.md](https://github.com/MarineCast/toolkit-meteorology/blob/main/CHANGELOG.md) together. Confirm `meteorology.__version__` matches wheel metadata.
- [ ] Confirm supported Python versions and binary wheels for the geospatial dependencies.
- [ ] Run `python -m pytest -q` and focused scientific fixtures.
- [ ] Run `ruff check src tests`, `meteorology catalog --check`, and `meteorology feature-policy --check`.
- [ ] Regenerate `docs/reference/variables.md` with `python -m meteorology.variables --markdown` and confirm no diff.
- [ ] Run `mkdocs build --strict`.
- [ ] Run `python -m build`, `python -m twine check dist/*`, and install the built wheel in a clean environment outside the checkout.
- [ ] From that wheel, run `meteorology --help`, `init`, `example-offline`, `validate`, and `import meteorology`.
- [ ] Verify the GitHub Actions test/release workflow on the exact commit to tag. Obtain owner review of changelog, source rights and scientific limitations.
- [ ] Create an annotated `vX.Y.Z` tag after review. The tag workflow builds and attaches sdist/wheel to a GitHub Release.
- [ ] Approve the protected `pypi` environment's Trusted Publishing job after verifying artifacts and tag. Verify the package page and an external `pip install toolkit-meteorology`.
- [ ] Confirm main-branch MkDocs build and GitHub Pages deployment at the documented URL.

The `pypi` job uses GitHub OIDC with no repository token. The repository owner must register the exact GitHub owner/repository/workflow as a PyPI Trusted Publisher, configure the `pypi` environment with required reviewers, and set the repository variable `METEOROLOGY_PYPI_TRUSTED_PUBLISHING=enabled` before tagging. GitHub Pages must be enabled with Actions as its build source. Do not publish from an arbitrary branch or retag a released version.

Provider acquisition, large regional data rebuilds, model comparisons and consumer integrations are **data release gates**, not automatic consequences of a software tag. See the [limitations](../limitations.md) and [migration history](../MIGRATION.md).
