# Meteorology Toolkit

<img src="docs/assets/banner.png" alt="Engraved weather station" width="100%">

One supported product: approved coastal R5 ERA5 weather. Install with
`python -m pip install .`, then set `MARINECAST_STUDY_CONFIG` to the approved
owner-managed study JSON and its registry/mask inputs.

```python
from meteorology import get_weather, ALLOWED_VARIABLES, check_weather

result = get_weather("2009-01-01", "2009-01-02", ALLOWED_VARIABLES,
                     output_dir="scratch/weather", cache_dir="/path/to/source-cache")
check_weather(result.catalog_path, study_config="/path/to/config/study.v1.json")
```

For local run defaults, copy `.env.example` to `.env` in the repo root:

```dotenv
DATA_DIR=../../Data/Meteorology/
START_DATE=2009-01-01
END_DATE=2026-10-10
VERBOSITY=True
```

Run `meteorology get` or `get_weather()` from that directory to use these defaults.
Explicit arguments override process environment, which overrides `.env`. Relative
`DATA_DIR` in the file resolves beside the file. `.env` is private and ignored;
only `.env.example` is committed. Progress goes to stderr; use `--no-verbose` or
`verbosity=False` to silence it. Study configuration and provider credentials are
still required separately. This example targets the current product directory;
use a scratch `DATA_DIR` for experiments. Merely editing settings runs no pipeline.

Allowed families: `temperature`, `dewpoint`, `relative_humidity`, `wind`,
`cloud_cover`, `pressure`, `precipitation`. All seven yield 14 daily and 10 hourly
fields. The current approved registry has 463 cells. Dates are inclusive UTC days.
The fast ECMWF geographic chunk archive is cached separately; calls replace their
requested output range with two Parquets and `catalog.json`.

```sh
meteorology get 2009-01-01 2009-01-02 --variables temperature wind --output-dir scratch/weather --cache-dir /path/to/source-cache
meteorology check scratch/weather/catalog.json --study-config /path/to/config/study.v1.json
```

This is retrospective reanalysis. Historical operational availability and the final
ERA5 versus preliminary ERA5T distinction are unknown. Unsupported values remain
null; no forecast qualification is implied.

[API and scientific contract](docs/regional-api.md) ·
[Current product and source vintages](docs/coastal-product.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Development](docs/DEVELOPMENT.md) ·
[Removed legacy interfaces](docs/MIGRATION.md)

The primary pipeline is `regional.py`, `regional_source.py`, and `compute.py`,
with small credential/registry helpers and retained transactional publication.
Legacy HRRR/CDS requests, astronomy, modeling, visualizers and their optional
extras have been removed. Git history preserves the previous implementation.
No current or retained data is removed by this source cleanup.
