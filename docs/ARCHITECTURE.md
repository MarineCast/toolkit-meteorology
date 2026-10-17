# Architecture

[Documentation index](index.md)

## Supported coastal path

`get_weather` → approved study/registry → `regional_source.Archive` →
`backfill_compute` reductions → `regional` publication → `check_weather`.

The three existing pipeline modules handle acquisition/cache, scientific reduction,
and API/output/checks. Shared `marine_reporting` validates the registry and mask;
`core.artifacts` provides recoverable publication. Exact membership is selected
before tables are written, so no rectangular output is required. The wheel imports
no sibling toolkit. The approved owner study/registry is an explicit external input.

The public API returns paths to two Parquets and a catalog. Source evidence and
pinned arrays live in a separate private cache. `check_weather` streams complete
UTC days; passing the study also verifies external membership. The current archive
uses two source vintages, so exact historical replay requires both retained caches.
There is no automatic source-epoch coordinator or retry policy. See the
[primary contract](regional-api.md#approved-coastal-api).

The modules below retain compatibility workflows; their generic family-manifest
claims do not imply that every field exists in a coastal catalog.

## Package organization

| Module | Responsibility |
| --- | --- |
| `regional`, `regional_source` | Primary coastal API/checks and ECMWF geographic Zarr acquisition, private local archive, wide hourly/daily H3 Parquets and small catalog |
| `cli` | Workspace selection, initialization and delegation to family commands |
| `config` | Validated product configuration |
| `study` | Optional portable shared-study v1 validation, selection and canonical provenance; no sibling imports |
| `spatial_support` | Deterministic full-bbox atmospheric H3 support |
| `surface_weather` | HRRR acquisition, identity validation, sampling, aggregation and inspection |
| `astronomy`, `daylight`, `lunar` | Deterministic astronomical calculations and publication |
| `artifacts` | Product manifests, checksums, schema contracts and source identity |
| `week_demo`, `hourly_weather.live` | Single-writer, budgeted NOAA week orchestration with resumable source bundles and normal hourly releases |
| `weather_summary` | Bounded-by-day daily/weekly export of validated hourly atmosphere, with native-H3 and equal-centroid regional statistics |
| `core.artifacts` | Atomic writes and recoverable family transactions |
| `core.config`, `core.geo`, `core.data` | Local config helpers, H3 utilities, schemas and dataset registry |
| `maintenance` | Catalog generation and acquisition benchmark |
| `modeling.feature_policy` | Optional inherited downstream column-selection policy |
| `resources/config` | Configuration and reference metadata copied by `init` |

## Data flow

```text
ECMWF geographic ERA5 → private regional cache → hourly/daily wide H3 Parquets + catalog

Configured bounds → atmospheric H3 support
                         ├→ HRRR download → validated R5 samples + inventory → daily weather
                         ├→ daylight integration → R4 daily + day-of-year lookup
                         └→ lunar calculations → R5 daily

Family products + manifests → inspectors / verification → downstream applications
Family schemas and materialized products → feature catalog → optional model-feature policy
```

The toolkit imports neither OrcaCast nor sibling toolkit source trees. Application-specific
occurrence modeling, interpretation and forecast presentation remain downstream concerns.

## Storage and publication

Source code and packaged defaults are installed with the distribution. Runtime configuration,
samples, generated tables, manifests and HTML belong to the selected external workspace.
Package-owned schemas define field types; the registry records keys, partitioning and dependencies.

Each producer stages its family artifacts before transactional replacement. A durable journal
supports recovery after interruption. This is a family-level guarantee, not a simultaneous release
of weather, daylight and lunar products. Callers must coordinate cross-family releases themselves.

Manifests carry software and method versions, an execution `run_id`, a content-derived `release_id`,
resolved configuration and its hash, source attribution, input/output checksums,
spatial and temporal support, units, formulas and limitations. The code fingerprint hashes the
installed Python/YAML files; an unrelated data-workspace Git revision is never used as producer
identity. An ordinary wheel can have a null Git revision while retaining a source hash.

## Public Python entry points

```python
from meteorology import (
    get_data, get_metric,
    load_config, build_spatial_support, download_surface_weather,
    build_surface_weather, build_daylight, build_lunar,
    export_daily_matrix, validate_product, freeze_release,
)
```

The regional APIs use explicit output/cache paths and need no initialized workspace.
For the configured family APIs, set `METEOROLOGY_WORKSPACE` outside the workspace. Loading configuration
and validation are read-only; build/download/freezing functions perform the same writes as their
CLI counterparts. Prefer these public entry points over internal helpers. Unused inherited
stage/artifact classes, H3 utilities and aliases, weather wrappers, and area-range helpers
have been removed; their old deep imports are no longer supported. The active dataset registry,
atomic writers, publication machinery and scientific product contracts are retained.
Read [workflows](WORKFLOWS.md) before invoking producers and [migration](MIGRATION.md) for the
remaining OrcaCast integration boundary.

For an explicit shared input, [shared-study](shared-study.md) documents the
production approval/registry gates and producer-owned paths under the relative
Data root. Full rectangle atmospheric companions remain distinct from marine
reporting membership. The packaged JSON schema is separately pinned in embedded
study provenance; actual source coverage remains independent of requested dates.


## Standalone request path

`capabilities` owns variable/input dependencies; `request` validates and plans
explicit bbox/source/UTC requests. The explicit-source getters delegate to that path; `get_data` and `get_metric`
use the separate regional path.
`request_adapters` reuses strict native source decoders, bounded HTTP/job clients,
point-block normalization and the existing validated HRRR nearest crosswalk.
`request_cache` owns immutable source/result identities, locking and disk-backed
`WeatherResult`; `request_cli` calls the same Python executor. Getters never invoke
the CLI or mutate process-global workspace/study environment variables.

This separate v1 request method/schema does not change family publication or the
shared-study/marine reporting gates. Only this checkout is affected. Full
[request semantics](request-api.md) and the [cleanup inventory](request-cleanup.md)
document compatibility and the retained advanced tools.

## Historical ERA5 executor

`backfill_plan` admits partial/monthly temporal selections and a single terminal
accumulation boundary. `backfill` coordinates independently leased source jobs,
bounded prefetch, immutable Parquet partitions and durable result pointers.
`backfill_resources` extends limits only for this executor and shares persistent
aggregate counters across workers. `backfill_compute` decodes once to bounded
numeric scratch, reduces daily native-grid data, then applies an optional cached
R5 crosswalk. `backfill_metrics` measures client job wait and transfer separately.
`backfill_cli` invokes the public executor without a subprocess or global study
environment changes. See [backfill contracts and recovery](era5-backfill.md).

Source locks cover one exact selection, not the whole namespace. Run locks stop
duplicate logical runs; publication locks cover a single partition. Output lives
at the explicit destination or the study-derived Data root, independently of
cache ownership. Existing HRRR/getter/family executors remain available.
