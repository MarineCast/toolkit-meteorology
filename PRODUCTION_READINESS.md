# Production readiness evidence

This is a local implementation review for proposed software version `0.1.0` on 2026-09-30. It does not certify a live NOAA data release, a deployed documentation site, or a published Python package.

## October 1 acquisition publication patch (build ID `pr1-acquisition-20261001`)

This PR 1 candidate is based on `origin/main` at
`7ed661953acaba41efc980dac4caa51928514451`; the PR head commit identifies the exact
source tested at handoff. The previous scientific corrections remain in the base. Before the
fix, two real-package synthetic-provider regressions reproduced N02: a rejected overlapping
six-to-eight-hour lag refresh changed six previously referenced January 3 sample files, and a
later provider failure during `overwrite=True` changed five. Canonical inventory and manifest
bytes stayed old, leaving their references invalid.

The candidate uses serialized-SHA-256 sample and crosswalk paths, a writer lock for the full
raw-workspace lifecycle, per-run working inventory/state, and the existing recoverable two-file
metadata publisher. `phase=committed` in its durable journal is the commit checkpoint. A
pre-commit interruption rolls back to old metadata; an interruption after that checkpoint
retains the new complete metadata. Canonical readers obtain a shared metadata snapshot or
report busy/recovery required. Frozen acquisition and weather releases copy all referenced
objects and validate after relocation. Old timestamp-addressed inventory paths remain readable.
No old referenced object is renamed or replaced by a refresh, and unreferenced candidates are
retained for manual inspection; no automatic object deletion is implemented.

| Gate | Local result on macOS arm64 / Python 3.12 |
| --- | --- |
| Full offline suite | PASS: 151 passed, one opt-in live NOAA test skipped |
| Acquisition publication cases | PASS: overlapping lag, late overwrite failure, candidate corruption, cancellation, hard process exits before and during promotion, concurrent writers, resume, full refresh and frozen relocation |
| Ruff; catalog/policy freshness; variable documentation diff; `git diff --check` | PASS |
| `mkdocs build --strict` | PASS; Material emitted its upstream MkDocs 2.0 advisory |
| Isolated `python -m build`; `python -m twine check dist/*` | PASS; build dependencies resolved from the package index |
| Fresh wheel environment outside the checkout | PASS: declared dependencies freshly resolved, `pip check`, CLI help/init, synthetic offline example, deep acquisition/weather validation, native matrix validation and policy selection (569 rows, 33 columns, R4/R5) |
| Live NOAA/Herbie, real-data workspace, release publication | NOT RUN |

Software version `0.1.0`, Arrow schemas, and acquisition/weather scientific method IDs stay
unchanged because the meteorological formulas and interpretation did not change. New acquisition
manifests declare `sample_storage=immutable-sha256-objects-v1`; content and configuration hashes
produce new release IDs. Existing releases keep their identities and must be interpreted with
their recorded method and storage paths. PR 2 geographic acceptance and PR 3 independent
scientific comparison remain separate work; this patch does not close those evidence gates.

## October 1 rereview correction (base `5dde17996021498e45750e7811f5b3aff24ef0d2`)

This section records the correction branch's local evidence. The exact candidate revision is
the Git commit containing this section; the pull request identifies its head SHA. On the
inspected base commit, [offline package checks](https://github.com/MarineCast/toolkit-meteorology/actions/runs/36831750073)
and the [documentation-site workflow](https://github.com/MarineCast/toolkit-meteorology/actions/runs/36831750078)
completed successfully. Those runs do not test the correction branch. The one-cycle live HRRR
smoke described in the rereview was author-reported for the earlier revision, not rerun here.

The correction branch enforces a continuous complete acquisition schedule, checks every retained
row against one lag policy, separates logical HRRR object identity from actual retrieval URI/time,
and retains conflicting migration evidence. It repairs the native matrix/policy handoff, DST
calendar selection, compact daylight semantics, civil-day hour limits, wind rotation magnitude,
and calm-direction validation. Acquisition, daily weather and daylight method IDs advance to
v3, v4 and v3. Previously generated releases keep their prior IDs and data; regenerate affected
releases before claiming these new contracts. See [archived validation](docs/archived-validation.md)
for the earlier validator revisions.

Local macOS arm64/Python 3.12 checks for this branch: the full offline suite
(141 passed, one opt-in live test skipped),
Ruff, catalog and
policy freshness, strict MkDocs build, source diff check, non-isolated sdist/wheel build, Twine
distribution check, and an installed-wheel smoke from outside the checkout. The wheel smoke
initialized a new workspace, generated and deeply validated synthetic acquisition, weather,
daylight, lunar and native daily-matrix artifacts; it also validated the matrix separately.
The wheel was installed without dependencies into a separate target directory and executed with
an existing dependency environment; this is not a fresh dependency resolution check.

Live Herbie/NOAA acquisition, a retained real-GRIB rotation reference, regional station/buoy
accuracy, independent solar/lunar ephemeris comparison, precipitation sensitivity, a historical
regional rebuild, migration on real legacy data, downstream OrcaCast integration, and an
as-issued forecast evaluation were **not run** for this correction. The
[scientific validation plan](docs/scientific-validation-plan.md) fixes the reference inputs,
alignment decisions and metrics needed before those accuracy claims can be made. Nearest-grid
distance remains QC context without a domain-wide acceptance threshold; extending the geographic
domain requires a separate footprint and distance policy.

## September 30 snapshot (historical)

### Supported

Package metadata targets Python 3.11–3.13 on macOS and Linux. POSIX file locking is used for product-family publication. The local installed-wheel check below used macOS arm64 and Python 3.12; the 3.11/3.13 and Linux jobs are configured in CI but have not yet run on this branch.

### Tested

| Check | Local result |
| --- | --- |
| `python -m pytest -q` | 105 passed, 1 opt-in live-provider test skipped, Python 3.12 |
| `ruff check src tests` | passed |
| `meteorology catalog --check` and `feature-policy --check` | passed |
| Generated variable inventory diff | passed |
| `mkdocs build --strict` | passed; Material prints an upstream MkDocs 2.0 advisory |
| `python -m build` | isolated sdist and universal wheel build passed |
| `python -m twine check` | both distributions passed |
| Direct `pip install .` build path | passed in the temporary clean-dependency environment |
| Clean wheel environment outside checkout | import/version parity, CLI help, init, synthetic offline build, matrix validation and `pip check` passed |
| Frozen weather release relocation | focused test passed after moving its release directory |

The first isolated build attempt lacked package-index access under the sandbox. After the temporary build environment received approved index access, the standard isolated build passed. Remote GitHub Actions results are pending.

### Scientific products

- H3 R4/R5/R6 atmospheric centroid support within a configured WGS84 box.
- Retrospective R5 daily surface weather from six HRRR f00 analyses and matched f01 precipitation-rate snapshots. Wind vectors are reduced through mean U/V components. Precipitation is a six-snapshot extrapolation, not a full 24-hour accumulation.
- Approximate R4 daylight and R5 lunar context with explicit nulls where polar-night or no-night ratios are undefined.
- An optional R4/R5 native-resolution daily matrix with unsupported family values null.

See [methodology](docs/methodology.md), [variable inventory](docs/reference/variables.md), and [limitations](docs/limitations.md) for source fields, units, formulas, missingness and interpretation limits. All offline fixtures are explicitly synthetic.

### Installation

The wheel was installed with declared dependencies into a fresh Python 3.12 environment in `/private/tmp`, then used from outside this checkout. `meteorology.__version__` equaled the installed distribution version (`0.1.0`), and `pip check` found no broken requirements. The acquisition extra (`herbie-data`, cfgrib, ecCodes) was not installed or exercised in that wheel environment.

### Documentation

MkDocs builds strictly without generated scientific datasets. The README and quickstart include an installed command path, offline example, real-HRRR dry run and scientific caveats. Pages deployment is configured but has not been observed live.

### Packaging

The distribution builds as sdist and `py3-none-any` wheel with packaged configuration templates and a canonical version source. `twine check` passes. Binary platform dependencies remain resolver-managed, and compatibility on every configured CI platform awaits remote runs.

### Release process

Push/PR CI checks Python 3.11, 3.12 and 3.13, science fixtures, lint, catalog drift, docs and a clean wheel example. A tag workflow accepts only `v` plus the canonical semantic software version, repeats quality and packaging checks, creates a GitHub Release, and offers PyPI OIDC publishing behind an explicit repository variable and protected environment. A main-branch Pages workflow builds and deploys the site. None of these remote workflows has run for the proposed release.

### Known limitations

- No live Herbie/NOAA acquisition, GRIB decode or archive compatibility check was run for this review. The one-cycle `pytest -m live` smoke is available for an explicitly budgeted provider check.
- No full regional historical rebuild, legacy parity gate, model comparison or downstream OrcaCast integration was run.
- Weather is retrospective model context; precipitation does not represent a validated 24-hour accumulation or forecast skill.
- Centroid-nearest source sampling, one configured timezone, approximate astronomy and explicit resolution differences limit interpretation.
- Deep validation targets the current method/schema versions. Retained older methods need their corresponding validator or a separately documented migration.

### Remaining manual repository/PyPI setup

1. Review this diff and run the configured GitHub Actions matrix on the exact commit proposed for tagging, including the remote clean wheel and Pages build.
2. Enable GitHub Pages with Actions as its source, then verify the deployed URL and navigation.
3. Register the PyPI Trusted Publisher for `MarineCast/toolkit-meteorology` and `.github/workflows/release.yml`; create a `pypi` environment with required reviewers. Set `METEOROLOGY_PYPI_TRUSTED_PUBLISHING=enabled` only when publication is authorized.
4. Run a bounded, explicit live HRRR acquisition/GRIB compatibility smoke test and record the provider/source identity. Broader regional rebuild and downstream consumer gates are separate data-release work.
5. After review, tag `v0.1.0` only if the owner accepts the remaining limits. No tag, GitHub Release, PyPI upload or Pages deployment was created here.
