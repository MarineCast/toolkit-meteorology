# Acquisition, build and inspection

[Documentation index](README.md)

These examples assume installation, an initialized workspace, and geography configured as described
in [setup](SETUP.md) and [configuration](CONFIGURATION.md). Commands use an explicit short interval.
They write products in the selected workspace.

## 1. Build atmospheric support

```sh
export METEOROLOGY_WORKSPACE=/path/to/weather
meteorology build spatial-support
```

Build support before weather acquisition or astronomy. All downstream families use these same
configured H3 cells at their respective resolutions.

## 2. Acquire and build weather

Install the acquisition extra before downloading. Preview requests first:

```sh
meteorology download surface-weather --start-date 2024-01-02 --end-date 2024-01-02 --dry-run
meteorology download surface-weather --start-date 2024-01-02 --end-date 2024-01-02 --workers 4
meteorology build surface-weather --start-date 2024-01-02 --end-date 2024-01-02
```

The download command performs network access; the build consumes validated local samples and
inventory. Worker count must be between 1 and 16. Successful samples and the working inventory
survive acquisition failures. Rerun the same command to resume. `--overwrite` explicitly replaces
already validated samples; it is not needed for normal resumption.

Canonical acquisition metadata is published only after the requested range is complete. Do not
substitute a partial working inventory for the canonical one. See [contracts](CONTRACTS.md) for
source identity, completeness and precipitation gates.

## 3. Build astronomy

```sh
meteorology build daylight --start-date 2024-01-02 --end-date 2024-01-02
meteorology build lunar --start-date 2024-01-02 --end-date 2024-01-02
```

Astronomy requires spatial support but no HRRR download. Builds use transactional family
publication and can replace existing family outputs. Treat date-limited runs as separate products;
do not assume they append safely to an existing multi-year product.

## 4. Inspect and verify

```sh
meteorology inspect spatial-support
meteorology inspect surface-weather --date 2024-01-02
meteorology inspect daylight --date 2024-01-02
meteorology inspect lunar --date 2024-01-02
meteorology verify --help
```

Inspectors validate manifest checksums before writing HTML. Rendering an HTML file is separate from
visually reviewing it. The additional weather time-series exporter is available through:

```sh
python -m meteorology.surface_weather.time_series_map --help
```

`meteorology verify` audits an existing HRRR rebuild and optionally compares legacy data. An absent
legacy archive is recorded as unavailable; it is not evidence of parity. Use its `--help` to select
the legacy root and report destination.

## 5. Refresh metadata

```sh
meteorology catalog
meteorology catalog --check
meteorology feature-policy
meteorology feature-policy --check
```

The catalog checks materialized schemas and marks absent products pending. The inherited model
policy excludes metadata and deterministic aliases from a default ecological-model matrix; it
does not remove scientific product columns or establish suitability for a particular species.
The catalog generator uses its declared standard product paths, so custom output locations need
explicit attention when generating metadata; it does not read the producer config to discover them.

## Maintenance and failure recovery

| Situation | Action |
| --- | --- |
| No configuration found | Initialize and select the same workspace for every command |
| Empty H3 support | Check bounds and whether the region contains centers at R4 |
| Acquisition incomplete | Review failures in the working inventory; resume the frozen request range |
| Schema, checksum or source-identity failure | Diagnose the input or provenance mismatch before publishing |
| Policy reported stale | Regenerate catalog, then policy, against the intended workspace |
| Interrupted publication | The family transaction journal supports recovery on the next run |

`meteorology benchmark` performs live acquisition comparisons. `meteorology migrate-legacy`
defaults to planning; `--execute` moves legacy artifacts after replacement validation. Neither is a
required setup step. The toolkit does not provide a whole-domain candidate release command.
