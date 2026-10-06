# Explicit shared study input

Meteorology remains independently installable. An optional MarineCast study v1
JSON selects the requested rectangle, UTC dates, R5 reporting resolution,
meteorology acquisition policy and portable Data root. No sibling package,
OrcaCast import, parent-directory search or host-specific default is used.

```sh
meteorology --study-config /path/to/config/study.v1.json study-preflight
```

`--study-config` takes precedence over `MARINECAST_STUDY_CONFIG`. With neither,
existing standalone producer configuration and workspace behavior are unchanged.
An explicit missing or invalid study fails; it never falls back to example bounds.
The JSON Schema is packaged locally from the pinned shared v1 contract. Duplicate
JSON keys, non-finite values, inconsistent geometry hashes, dates and absolute
Data roots are rejected. `storage.data_root` resolves relative to the study file.
No data directory is created by preflight.

`study-preflight` permits a proposed domain for planning and reports the complete
parsed study, canonical config and geometry hashes, raw-file/schema checksums,
requested dates, buffers and resolved Data root. It makes no provider requests
and does not infer actual source coverage or certify reporting membership.

The public `load_meteorological_config(..., study_config=PATH, planning=True)`
provides the same planning selection. Ordinary producers use the default
`planning=False` and reject a proposed domain **before writing or acquiring**.
They also reject a pending reporting registry. Approval alone does not make a
pending marine mask/membership valid. Scientific mask and registry-artifact
qualification remain separate from this configuration adapter.

Selected shared outputs are mapped under `Data/meteorology/raw/` and
`Data/meteorology/native/`. The original standalone paths remain unchanged.
The study request `[2009-01-01,2027-01-01)` maps to inclusive producer dates
2009-01-01 through 2026-12-31 in UTC. This expresses the requested window only;
source acquisition and complete-day validation must still establish actual
coverage. Explicit bounded producer date arguments retain their actual interval
in manifests. Future, unpublished and pre-HRRR days are not manufactured.

Native atmospheric companions continue to retain full rectangle center support
at their native R4/R5/R6 resolutions; they do **not** claim to implement the
positive-area marine reporting mask. The shared registry's R5 reporting target
must be independently qualified before a final marine reporting export. This
patch does not change the complete-day hourly contract, add an ERA5 adapter,
or certify a marine mask.

Every newly built native family embeds `resolved_config.shared_study`, including
the complete canonical study, config/geometry identity, requested window, resolved
Data root and producer buffer policy. These fields participate in resolved-config
and release hashes and are checked without reopening an original host path.
Support reuse requires the same study identity. Summaries reject mixed study
identities and inherit their source identity. Actual native time coverage,
retrieval evidence and source/checksum references remain separate existing
manifest fields. The embedded identity has no operational-availability claim.

`legacy_bbox_padding_degrees` is preserved as an explicit acquisition policy,
not interpreted as a geodesic offshore buffer. The accompanying
`native_grid_policy` is retained in provenance. Existing native-footprint and
nearest-distance validation remain authoritative; this adapter does not prove
that a fixed crop contains all future interpolation stencils. Provider-native
stencil/crop qualification and aggregate download/retention budgets are required
before an expanded-domain acquisition.

Offline checks:

```sh
python -m pytest -q tests/test_shared_study.py tests/test_config_and_astronomy.py tests/test_manifests.py
python -m pytest -q
```

An installed-wheel preflight must be checked from outside the source checkout,
including packaged schema presence and the same canonical hashes. Provider
acquisition is excluded from these checks.
