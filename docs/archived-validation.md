# Validate an archived meteorology release

Current `meteorology validate` deeply validates only current method IDs. It can inspect
known older manifests and checksums, but a deep result for an older release must come
from the producer revision that generated that method. Do not relabel an old release
with a newer method ID.

| Producer revision | Acquisition | Daily weather | Daylight | Matrix |
| --- | --- | --- | --- | --- |
| `39ecf4c7cb1a38fc02878889d0fec8523e1742f7` | `hrrr_f00_f01_nearest_sample_v1` | `hrrr_surface_daily_v2` | `daylight_astronomy_v2` | `native_resolution_daily_matrix_v1` |
| `5dde17996021498e45750e7811f5b3aff24ef0d2` | `hrrr_f00_f01_earth_wind_sample_v2` | `hrrr_surface_daily_earth_wind_v3` | `daylight_astronomy_v2` | `native_resolution_daily_matrix_v2` |
| `bb2b725fd6aeede35264613ab812d0b5e2afe0e9` (PR 1) | `hrrr_f00_f01_earth_wind_sample_v3` | `hrrr_surface_daily_earth_wind_v4` | `daylight_astronomy_v3` | `native_resolution_daily_matrix_v2` |

PR 2 changes the acquisition method to `hrrr_f00_f01_earth_wind_sample_v4` and adds
crosswalk/inventory spatial-identity columns. The PR 1 revision is the pinned reader
for retained v3 acquisition artifacts. A v3 inventory is not proof that every target
passed native-footprint, crop-completeness and point-distance checks; regenerate it
with the new acquisition method before making that claim. Unchanged daily weather,
daylight, lunar and matrix methods retain their existing identities.

For a retained release, identify its `method_version` in the manifest. Use a
separate checkout and Python environment pinned to the matching revision, then
run its installed CLI against the retained release. For example, from the
repository root for the second row:

```sh
git worktree add --detach /private/tmp/meteorology-validator-5dde179 5dde17996021498e45750e7811f5b3aff24ef0d2
python3.12 -m venv /private/tmp/meteorology-validator-5dde179-venv
/private/tmp/meteorology-validator-5dde179-venv/bin/python -m pip install '/private/tmp/meteorology-validator-5dde179[test]'
/private/tmp/meteorology-validator-5dde179-venv/bin/meteorology --workspace /path/to/retained-workspace validate --manifest /path/to/retained/MANIFEST.json
```

If the release is a native daily matrix, use `validate --daily-matrix
/path/to/retained/matrix.parquet` and retain its companion
`.parquet.manifest.json`. Supply the release's original configuration and all
checksum-addressed inputs in the workspace expected by its manifest. Keep the
original manifest, command output, Python version, `pip freeze`, exact Git SHA,
and hashes of the validated files as the validation record. An isolated
install with unconstrained dependencies is **revision pinned but not a lockfile**;
record the resolved versions or install from a reviewed environment lock if one
is available. A failed read caused by missing or relocated files is not evidence
that the historic numerical product failed its scientific contract.

These entrypoints document a reproducible handoff. They do not assert that every
historic release has been revalidated, or that a released dataset from either
revision was available to this code review.
