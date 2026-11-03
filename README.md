# Meteorology Toolkit

<img src="docs/assets/banner.png" alt="Engraved weather station" width="100%">

Three distinct coastal R5 products: ERA5 Weather, calculated Solar Cycle and
ERA5 Solar Radiation. Install with
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
CDSAPI_KEY=
MIN_LAT=45.9
MAX_LAT=51.5
MIN_LON=-129.7
MAX_LON=-121.5
START_DATE=2009-01-01
END_DATE=2026-10-10
REBUILD=false
OUTPUT_DIRECTORY=../../Data/Meteorology
MARINECAST_STUDY_CONFIG=../../config/study.v1.json
```

From the repo, run `meteorology preflight`, then `meteorology get`,
`meteorology solar` or `meteorology radiation` for the desired product.
Bounds select approved coastal/water R5 cell centres only. The example preserves
the full approved region. All commands share dates, bounds and mode; solar products
use their named subdirectories under OUTPUT_DIRECTORY. Changing a saved region requires rebuild.
Arguments override process environment, then the file. Use `--env-file PATH`
for another file. File-relative paths resolve beside that file. The private
`.env` is ignored and excluded from packages. Put the token in `CDSAPI_KEY`;
an empty value allows credential-file fallback. Optional VERBOSITY defaults to
false and METRICS to all weather families.
See [configuration and credentials](docs/regional-api.md#local-run-defaults).
Editing settings runs no pipeline; use a scratch destination for experiments.

Allowed families: `temperature`, `dewpoint`, `relative_humidity`, `wind`,
`cloud_cover`, `pressure`, `precipitation`. All seven yield 14 daily and 10 hourly
fields. The current approved registry has 463 cells. Dates are inclusive UTC days.
The fast ECMWF geographic chunk archive is staged temporarily in `Data/Meteorology/tmp/`.
After final-product readback, owned downloads/arrays are removed; source hashes and
receipts remain in the catalog. Failed runs retain completed objects for resumption.
Small persistent locks and recovery records remain under `tmp/` after payload cleanup.
Without OUTPUT_DIRECTORY, the approved study Data root is used. Data destinations inside
the toolkit checkout are rejected; the old working area is not migrated automatically. An explicit `cache_dir` is a work base with an owned child;
existing shared files there are preserved. With `REBUILD=False`
(the default), runs validate existing files, preserve their dates and acquire
missing dates and new metrics; a request with all dates/metrics already covered
does no acquisition or publication. Disjoint
requests also fill the intervening dates to keep a continuous product. `REBUILD=True`
replaces the product with the requested dates and metrics. Either mode creates the
product if absent. Newly requested metrics are backfilled across the entire saved range; existing
columns and valid nulls are retained.

```sh
meteorology get 2009-01-01 2009-01-02 --variables temperature wind --output-dir scratch/weather --cache-dir /path/to/source-cache --no-verbose --no-rebuild
meteorology check scratch/weather/catalog.json --study-config /path/to/config/study.v1.json
```

Run `meteorology preflight` to check settings without creating output, loading the
study, or accessing providers. Weather requires both dates; optional settings
have validated defaults. One preflight reports
missing/invalid settings together: dates/order, allowed unique metrics, booleans,
and usable output path/parent permissions. It runs automatically before work.

This is retrospective reanalysis. Historical operational availability and the final
ERA5 versus preliminary ERA5T distinction are unknown. Unsupported values remain
null; no forecast qualification is implied.

[API and scientific contract](docs/regional-api.md) ·
[Current product and source vintages](docs/coastal-product.md) ·
[Reproducibility review and layer maps](docs/reproducibility.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Development](docs/DEVELOPMENT.md) ·
[Removed legacy interfaces](docs/MIGRATION.md)

The primary pipeline is `regional.py`, `regional_source.py`, and `compute.py`,
with small credential/registry helpers and retained transactional publication.
Legacy HRRR/CDS requests, former astronomy interfaces, modeling, visualizers and their optional
extras have been removed. Git history preserves the previous implementation.
No current or retained data is removed by this source cleanup.

## Solar Cycle

```python
from meteorology import get_solar_cycle, check_solar_cycle
solar = get_solar_cycle(study_config="/path/to/config/study.v1.json")
check_solar_cycle(solar.catalog_path, study_config="/path/to/config/study.v1.json")
```

`meteorology solar --study-config PATH` creates `Data/Meteorology/solar-cycle/`
with its own catalog, hourly solar elevation/azimuth/light phase, and daily
sunrise/sunset, civil/nautical/astronomical dawn/dusk and phase durations. It uses
selected R5 cell centres and configured dates without downloads. Without
configured dates it uses the study range. Append extends the continuous saved
range, preserving retained rows; rebuild replaces the requested range. Durations partition UTC days; sunset may precede sunrise within
one date. These are sea-level unobstructed-horizon calculations. See the
[Solar Cycle contract](docs/solar-cycle.md) and
[radiation/waves investigation](docs/radiation-and-waves.md).

## Solar Radiation

```python
from meteorology import get_solar_radiation, check_solar_radiation
radiation = get_solar_radiation("2009-01-01", study_config="/path/to/config/study.v1.json")
check_solar_radiation(radiation.catalog_path, study_config="/path/to/config/study.v1.json")
```

`meteorology radiation 2009-01-01 --study-config PATH` discovers the latest complete
source day and creates `Data/Meteorology/solar-radiation/` with a separate catalog,
hourly downward shortwave/direct horizontal energy and irradiance, and complete
UTC daily totals/means. Explicit `--allow-incomplete-final-day` can retain one
missing terminal interval as null. Append preserves retained vintages and nulls;
explicit rebuild replaces the selected range. See the
[Solar Radiation contract](docs/solar-radiation.md).

Solar Cycle and Solar Radiation retain their producer-code identity guard: saved
products from different producer code require explicit rebuild before extension.
