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
