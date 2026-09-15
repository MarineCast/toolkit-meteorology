# OrcaCast meteorology extraction — 2026-09-15

## Result and reference toolkits

`toolkit-meteorology` now owns the `meteorology` Python package and command. The extraction follows
`toolkit-seascape` and `toolkit-viewshed`: src-layout packaging, declared dependencies, toolkit-owned
support helpers, configuration resources, source contracts, offline scientific tests, and an
external workspace. It does not import OrcaCast, require a sibling checkout, or use species data.

| Before | After | Why |
| --- | --- | --- |
| `orcacast.domains.environment.meteorological` | `meteorology` | Independent producer ownership |
| Application config/geometry/artifact imports | Minimal toolkit-owned `core` modules | Standalone installation |
| Config and outputs tied to an application checkout | CWD, `METEOROLOGY_WORKSPACE`, or `--workspace` | Wheel-safe external workspaces |
| Source hash from the application workspace | Installed package source/resource hash | Identify actual producer code; avoid hashing an empty tree |
| Application data producer registry | 10 local dataset registrations | Keys, schemas and dependencies remain inspectable |
| Application feature catalog and generator | Toolkit catalog and installed maintenance module | Metadata follows producer ownership |
| Application collect/build/migrate commands | Per-family `meteorology` CLI routes | Explicit acquisition and offline processing |

The 31 original producer Python modules, astronomy formulas, schemas, f00/f01 HRRR semantics,
precipitation estimate, source validation, resumption and per-family transactional publication were
retained. No provider replacement or scientific recalculation was introduced. The copied generic
helpers remain in OrcaCast for other domains. Configuration data paths retain their established
relative layout within whichever workspace the caller selects.

`migration-inventory.json` records the originating Git revision, working-tree source SHA-256 values,
destination paths and destination SHA-256 values for 60 direct transfers. Registry extraction is
recorded separately. The source working tree had pre-existing changes in the weather build,
download, sampling, time-series-map test, catalog and catalog generator; those live versions moved
with this extraction. No reset to HEAD was performed.

Catalog/policy files were regenerated against the empty toolkit workspace, so materialization
status is pending rather than inherited evidence from OrcaCast's datasets. Product IDs, schemas,
column meanings and scientific policy behavior remain compatible. The inherited ecological-model
feature-selection helper remains optional; its choices do not define the scientific products.

## OrcaCast retirement and integration boundary

Removed 47 producer-owned files and six workflow stages from OrcaCast, plus the old meteorological
collect/build/legacy-migration commands and config/catalog producer registrations. The producer
package no longer remains as a second implementation. Generic shared helpers and unrelated
worktree changes remain. Added `docs/meteorology-extraction.md` there to explain the boundary.

As with the prior seascape extraction, application integration remains deferred. Application-owned
consumer code and its tests remain in OrcaCast. These live source references need integration:

- `features/environmental_baseline.py`: old meteorological artifact-helper import.
- `domains/human/activity_and_effort/land_reporting_opportunity/dynamic.py`: old daylight fallback import.
- App/catalog readers, model-policy references, research notebooks and configured product paths
  must resolve toolkit artifacts explicitly. The old generated environment catalog moved with its producer.

Those application paths are not claimed runnable after extraction. The retained modeling-consumer
test is application-owned. No full OrcaCast model, forecast or application test suite was run.
The standalone CLI offers existing individual family transactions, not a replacement for the
application's old cross-domain candidate orchestration or a new whole-domain release gate.

## Executed validation

- **92 passed** in the standalone offline suite on Python 3.14, including the original scientific,
  acquisition identity, partial-failure/recovery, manifest, schema, astronomy and HTML-generation tests.
- New checks cover external workspace initialization, preservation of user edits, environment
  restoration, source identity independent of CWD, packaged configuration parity, registry dependencies,
  application-free imports, and all 14 delegated CLI help routes.
- Built and installed a regular wheel in `/tmp/meteorology-install-check`; `pip check` passed.
  All **92 tests also passed against the installed wheel** with source PYTHONPATH unset.
  This environment reused installed third-party scientific libraries. Fresh dependency downloads
  and a minimal clean-room environment were not exercised.
- From `/tmp`, **52 installed submodules imported with OrcaCast imports blocked**. Synthetic H3
  support, one-day daylight and lunar products built through the installed CLI. All three manifests
  passed input/output checksum validation. Installed catalog and policy generation/check passed.
- Source parses using Python 3.11 syntax. CI is configured for Python 3.11 and 3.14; remote CI and
  a local Python 3.11 runtime were not executed.
- OrcaCast: all **7 remaining workflow stages** resolve; its **49-entry application registry**
  and project/data configuration documents load; `data build --help` loads without meteorology.
- Transfer hashes and Git whitespace checks pass. MarineCast organization documentation was
  updated to reflect the implementation; repository URLs were checked against configured remotes.

Live NOAA/Herbie access, actual GRIB decoding, complete regional rebuilding/equality comparison,
visual inspection of rendered maps, production release promotion and downstream application
integration were not executed. Existing raw sources, processed products and research outputs
remain at their original locations. No datasets were copied into the toolkit, redistributed,
relabelled or rebuilt. No commits or pushes were made.
