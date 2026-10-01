# Command reference

All commands accept `--workspace PATH` **before** the subcommand. `METEOROLOGY_WORKSPACE` is an alternative; otherwise paths resolve from the current directory. `init` copies packaged configuration without replacing existing files.

| Command | Purpose | Network/write effect |
| --- | --- | --- |
| `meteorology init` | Create workspace configuration | Writes missing templates only |
| `meteorology stages` | Show build order | Read only |
| `meteorology variables --json` | Inspect canonical variable inventory | Read only |
| `meteorology download surface-weather --dry-run ...` | Preview date range, cycles and destination | No network/write |
| `meteorology download surface-weather ...` | Acquire validated HRRR f00/f01 samples | Network and raw-data writes |
| `meteorology build spatial-support` | Build R4/R5/R6 H3 support | Product writes |
| `meteorology build surface-weather` | Build strict R5 daily weather | Product writes; requires complete acquisition |
| `meteorology build daylight` / `build lunar` | Build deterministic astronomy | Product writes; no network |
| `meteorology example-offline` | Build synthetic one-day full example | Writes a fresh workspace; no network |
| `meteorology validate --manifest PATH` | Validate a family release | Read only, optional JSON result write |
| `meteorology validate --daily-matrix PATH` | Validate combined native-resolution matrix | Read only, optional JSON result write |
| `meteorology validate-science --reference-bundle PATH --protocol PATH --output-dir PATH ...` | Compare frozen releases to separate references | Offline; writes a new evidence directory |
| `meteorology inspect FAMILY` | Render a manifest-validated HTML view | HTML write |
| `meteorology export-daily-matrix --manifest ... --output PATH` | Combine native daily families | Writes one Parquet; refuses an existing output |
| `meteorology freeze-release --manifest PATH --output-root PATH` | Copy validated family and declared inputs to `release_id` directory | Potentially large copy; refuses an existing release |
| `meteorology catalog` / `feature-policy` | Regenerate reference metadata | Writes configured YAML |
| `meteorology verify` / `benchmark` / `migrate-legacy` | Specialized parity/maintenance workflows | Read each command's help and [workflows](../WORKFLOWS.md) first |

Run `meteorology COMMAND --help` or `meteorology build FAMILY --help` for the exact options. Invalid dates/configuration, missing GRIB dependencies and checksum mismatches return actionable errors. A download over seven local days requires `--allow-large-download`; `--dry-run` is always available without that flag. `--workers` is constrained to 1–16.

`validate` prints a JSON result, exits 0 on success and 1 on validation failure. `--json-output PATH` saves the same result for automation. A synthetic example is always marked as synthetic in its source manifest and weather provenance.
For the `validate-science` input schemas, release selection and evidence limits, see the
[offline scientific comparison workflow](../scientific-validation-workflow.md).
