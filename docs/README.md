# Meteorology documentation

The `toolkit-meteorology` distribution installs the `meteorology` package and CLI. It produces
retrospective HRRR weather, deterministic daylight and lunar features, and atmospheric H3 support.

## Start here

1. [Install and initialize a workspace](SETUP.md).
2. [Choose geography, dates and output locations](CONFIGURATION.md).
3. [Acquire, build, inspect and validate products](WORKFLOWS.md).
4. [Understand product keys, units and limitations](PRODUCTS.md).

## Reference

| Guide | Contents |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Package structure, dependency direction, publication and provenance |
| [Scientific contracts](CONTRACTS.md) | HRRR sampling, precipitation semantics, missingness and publication gates |
| [Sources and rights](DATA_SOURCES.md) | Provider attribution, acquisition dependencies and astronomy limitations |
| [Development](DEVELOPMENT.md) | Tests, wheel checks, resources and maintenance commands |
| [Migration report](MIGRATION.md) | Extraction inventory, executed checks and remaining application integration |
| [Transfer inventory](migration-inventory.json) | Source and destination hashes at extraction time |

Future-weather forecasting is not implemented by this extraction. A successful installation or
synthetic test does not establish provider availability or a complete regional build. See the
migration report for the checks that were actually executed.
