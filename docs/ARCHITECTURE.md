# Architecture

[Documentation index](README.md)

## Package organization

| Module | Responsibility |
| --- | --- |
| `cli` | Workspace selection, initialization and delegation to family commands |
| `config` | Validated product configuration |
| `spatial_support` | Deterministic full-bbox atmospheric H3 support |
| `surface_weather` | HRRR acquisition, identity validation, sampling, aggregation and inspection |
| `astronomy`, `daylight`, `lunar` | Deterministic astronomical calculations and publication |
| `artifacts` | Product manifests, checksums, schema contracts and source identity |
| `core.artifacts` | Atomic writes and recoverable family transactions |
| `core.config`, `core.geo`, `core.data` | Local config helpers, H3 utilities, schemas and dataset registry |
| `maintenance` | Catalog generation and acquisition benchmark |
| `modeling.feature_policy` | Optional inherited downstream column-selection policy |
| `resources/config` | Configuration and reference metadata copied by `init` |

## Data flow

```text
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

Manifests carry resolved configuration and its hash, source attribution, input/output checksums,
spatial and temporal support, units, formulas and limitations. The code fingerprint hashes the
installed Python/YAML files; an unrelated data-workspace Git revision is never used as producer
identity. An ordinary wheel can have a null Git revision while retaining a source hash.

## Public Python entry points

```python
from meteorology.config import load_meteorological_config
from meteorology.spatial_support.build import build_meteorological_spatial_support
from meteorology.surface_weather.download import download_surface_weather
from meteorology.surface_weather.build import build_surface_weather
from meteorology.daylight.build import build_daylight
from meteorology.lunar.build import build_lunar
from meteorology.artifacts import load_manifest
```

Set `METEOROLOGY_WORKSPACE` before invoking these APIs outside the workspace. Loading configuration
is read-only; build/download functions perform the same writes as their CLI counterparts.
Read [workflows](WORKFLOWS.md) before invoking producers and [migration](MIGRATION.md) for the
remaining OrcaCast integration boundary.
