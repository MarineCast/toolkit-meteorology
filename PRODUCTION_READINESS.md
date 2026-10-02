# Production readiness evidence

## Current status, 2026-10-02

The six-snapshot retrospective daily family remains the published producer
contract. The [implementation tracker](docs/IMPLEMENTATION_TRACKER.md) records
O01–O03 corrections, frozen-reference recomputation, candidate hourly/interval/RONI
helpers, and separate implementation, source-compatibility and empirical gates.
Passing offline package tests does not establish full-domain or precision-viewing
scientific acceptance. No new hourly, accumulation, climate or binational
release has been published.

This is a local implementation review for proposed software version `0.1.0` on 2026-09-30. It does not certify a live NOAA data release, a deployed documentation site, or a published Python package.

## October 1 geographic acceptance and independent pilot candidate

This candidate also addresses roadmap PR-03 and PR-04. Live
HRRR decoding checks the configured study box against the native grid-edge
polygon, the selected crop against grid-derived spacing, and every H3 support
centroid against that polygon and a nearest-point allowance. Acquisition v4
and weather v5 record this new spatial policy. Existing rows from an earlier
policy require a full frozen-range `--overwrite` reacquisition before a current
HRRR release can claim it.

The former example northwest corner at 50.00°N, 125.80°W failed that gate on
the decoded 2024-01-02 08 UTC NOAA cycle. The example now ends at 49.70°N;
all 450 R5 cells map within 1.95 km of a source point, against a 4.25 km
grid-derived allowance. This was a bounded source check, not a canonical
acquisition or release build.

Frozen independent references and rerunnable comparators are in
[`validation/`](validation/). Ten USNO cases passed the prespecified daylight
and matched-noon lunar illumination limits. Another 288 JPL Horizons hourly
altitudes passed the prespecified solar/lunar altitude limits, and all six
sampled UTC days agreed on dark-and-moon-visible hour counts. Twenty-four exact
UTC hours at NDBC buoy
46088 produced descriptive temperature, pressure, wind and gust errors. The
weather pilot has one buoy and one day, no sensor-height correction, and no
regional acceptance threshold. The [scientific validation plan](docs/scientific-validation-plan.md)
gives the numbers, source links and remaining coverage work. PR-05 through
PR-09 and full regional accuracy validation remain open.

Final local checks: 167 offline tests passed and one opt-in live test was
skipped; that live NOAA/Herbie test passed separately. Ruff, catalog and
feature-policy freshness, generated variable docs, strict MkDocs,
`git diff --check`, package build and Twine checks passed. An installed-wheel
synthetic run outside the checkout deeply validated acquisition, weather,
daylight, lunar and a 514-row native daily matrix. The local wheel check used
an existing dependency environment, so it is not a fresh resolver test.

## October 1 consumer snapshot and field-contract checkpoint

This earlier checkpoint started from `f5f2b1c08240c811bbdbba57e3e71e793245ebda` on `main`.
At that point, its changes were uncommitted and had no remote CI result.
The changes address roadmap PR-01 and PR-02: weather builds retain consumed acquisition and
support metadata, matrix export and release freezing pin source product generations, custom
acquisition manifest names use the same reader rule, and all current product readers/producers
share one hard field-limit registry. Pressure 700–1200 hPa is a hard range, while 800–1100 hPa
is a reported regional diagnostic. Current acquisition/weather manifests and daily matrices
declare `meteorology-field-contract-v1`; earlier metadata remains checksum-readable but needs
its historical deep validator.

The local macOS arm64 / Python 3.12 offline suite passed with 158 tests; the opt-in live NOAA
test was skipped. New tests force an acquisition refresh during weather build and matrix export,
attempt a weather rebuild during freezing, exercise custom acquisition metadata, and validate
pressure values in the diagnostic band through build, validation and freeze. Ruff, catalog and
model-policy freshness, generated variable docs, strict MkDocs, `git diff --check`, non-isolated
sdist/wheel build, Twine checks, and an outside-checkout wheel-target synthetic workflow passed.
That wheel target reused an existing dependency environment; it was not a fresh resolver test.
The initial isolated build could not reach the package index in the sandbox; the non-isolated
build used locally installed `setuptools` and `wheel`.

At this earlier checkpoint, roadmap PR-03 through PR-09 remained open. No native-footprint domain report, retained real-GRIB
reference, station/buoy comparison, independent astronomy benchmark, hourly field-era evidence,
interval-accumulation source, or regional resource budget was produced in this working tree.
No historical backfill, real-data mutation, release, PyPI upload or live provider check was run.

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
