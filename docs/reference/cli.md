# Command reference

## Historical ERA5 backfill

```sh
meteorology backfill --start-date 2009-01-01 --end-date 2018-12-31 \
  --bbox -129.7 45.9 -121.5 51.5 --workers 2 --dry-run
```

Remove `--dry-run` after CDS account/token/manual terms setup to acquire the
default daily atmospheric variables. Add `--output-dir` and `--cache-dir` for
explicit locations. The [backfill guide](../era5-backfill.md) documents variable,
frequency, native/R5 support, resource, retention and resume options. Inclusive
multi-year dates are automatically split into safe monthly/partial-month jobs.
`--study-config` can derive dates, bbox and Data-root destinations.

## Selective weather requests

These commands use the shared Python request path and need no initialized
workspace or study configuration:

```sh
meteorology sources
meteorology variables --data-source era5
meteorology get temperature --start-date 2024-01-01 --end-date 2024-01-01 \
  --bbox -124 48 -123 48.5 --data-source hrrr --dry-run
meteorology get weather --variables temperature wind cloud_cover \
  --start-date 2024-01-01 --end-date 2024-01-01 --bbox -124 48 -123 48.5 \
  --data-source hrrr --cache-dir ./weather-cache --output weather.parquet
```

The last command performs provider access unless a complete compatible result
is cached. `--dry-run` previews exact UTC boundaries, dependencies, support, cache
status and caps without network access or writes. Execution requires `--output`.

| Option | Meaning / default |
| --- | --- |
| `--data-source hrrr\|era5` | Required explicit source; no fallback or blending |
| `--start-date`, `--end-date` | Required inclusive date-only UTC days; at most seven days |
| `--bbox WEST SOUTH EAST NORTH` | Required WGS84 rectangle; no wrapping/dateline bbox |
| `--variables NAME ...` | Required only with `get weather`; single-variable commands select their own dependencies |
| `--frequency daily\|hourly` | Daily by default |
| `--spatial-output h3\|native` | HRRR defaults to H3 R5; ERA5 defaults to native and rejects H3 |
| `--h3-resolution 5` | Qualified HRRR R5 support only |
| `--cache-dir PATH` | Explicit retained source/result cache location |
| `--output FILE` | Stream a Parquet export with embedded provenance |
| `--refresh` | Promote a validated complete source generation, preserving history |
| `--result-id` | Pin a compatible historical result; mutually exclusive with refresh |
| `--retained-cache-mib` | Persistent retained-cache namespace quota; default unbounded, no eviction |
| `--credential-env-file PATH` | Explicit ERA5 credential alias file; see resolver precedence in the request guide |
| `--memory-mib`, `--staging-mib`, `--transfer-mib` | Caps default to 1024, 2048 and 512 MiB respectively |
| `--max-requests`, `--max-seconds` | Caps default to 1024 operations and 3600 active seconds |

Caps are cooperative upper bounds, not payload-size predictions. The
[weather request guide](../request-api.md) specifies supported quantities,
credentials, time/space semantics, fixture options and errors.
`meteorology get --help` shows the complete parser options. Legacy `variables`
without `--data-source` retains its family inventory output.

## Configured family commands

Use `--workspace PATH` **before** the subcommand. `METEOROLOGY_WORKSPACE` is an
alternative; otherwise paths resolve from the current directory. `init` copies
packaged configuration without replacing existing files.

| Command | Purpose | Network/write effect |
| --- | --- | --- |
| `meteorology init` | Create workspace configuration | Writes missing templates only |
| `meteorology stages` | Show build order | Read only |
| `meteorology variables --json` | Inspect canonical variable inventory | Read only |
| `meteorology download surface-weather --dry-run ...` | Preview date range, cycles and destination | No network/write |
| `meteorology download surface-weather ...` | Acquire validated HRRR f00/f01 samples | Network and raw-data writes |
| `meteorology download hourly-weather --date DATE --decoded-dir PATH --dry-run` | Preview true f00 hourly cycles and retained input bytes | No network/write |
| `meteorology download hourly-weather --date DATE --decoded-dir PATH` | Ingest retained decoded f00 grids into immutable hourly acquisition | Raw-data writes; no network |
| `meteorology build spatial-support` | Build R4/R5/R6 H3 support | Product writes |
| `meteorology build surface-weather` | Build strict R5 daily weather | Product writes; requires complete acquisition |
| `meteorology build hourly-weather` | Build separate strict R5 hourly atmosphere | Product writes; requires complete hourly acquisition |
| `meteorology build daylight` / `build lunar` | Build deterministic astronomy | Product writes; no network |
| `meteorology example-offline` | Build synthetic one-day full example | Writes a fresh workspace; no network |
| `meteorology validate --manifest PATH` | Validate a family release | Read only, optional JSON result write |
| `meteorology export-weather-summary --manifest PATH --output-dir PATH` | Export daily/weekly native-H3 and regional statistics; optional `--as-of-utc` | Reads validated hourly releases; writes a new summary and manifest |
| `meteorology validate --daily-matrix PATH` | Validate combined native-resolution matrix | Read only, optional JSON result write |
| `meteorology inspect FAMILY` | Render a manifest-validated HTML view | HTML write |
| `meteorology inspect hourly-weather` | Print deep-validated hourly lineage and coverage JSON | Read only |
| `meteorology export-daily-matrix --manifest ... --output PATH` | Combine native daily families | Writes one Parquet; refuses an existing output |
| `meteorology freeze-release --manifest PATH --output-root PATH` | Copy validated family and declared inputs to `release_id` directory | Potentially large copy; refuses an existing release |
| `meteorology catalog` / `feature-policy` | Regenerate reference metadata | Writes configured YAML |
| `meteorology verify` / `benchmark` / `migrate-legacy` | Specialized parity/maintenance workflows | Read each command's help and [workflows](../WORKFLOWS.md) first |

Run `meteorology COMMAND --help` or `meteorology build FAMILY --help` for the exact options. Invalid dates/configuration, missing GRIB dependencies and checksum mismatches return actionable errors. A download over seven local days requires `--allow-large-download`; `--dry-run` is always available without that flag. `--workers` is constrained to 1–16.

`validate` prints a JSON result, exits 0 on success and 1 on validation failure. `--json-output PATH` saves the same result for automation. A synthetic example is always marked as synthetic in its source manifest and weather provenance.

`meteorology demo-hourly-week --output <directory> [--start-date YYYY-MM-DD]
[--max-requests 1600] [--max-bytes 4294967296] [--dry-run]` runs the
[bounded live one-week example](../live-week-demo.md). The start must be a Monday;
dry-run issues no requests and creates no workspace.
