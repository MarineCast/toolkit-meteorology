# Coastal weather API

## Approved coastal API

Install the core package with `python -m pip install .` using Python 3.11–3.13
on macOS/Linux. Set `MARINECAST_STUDY_CONFIG=/path/to/config/study.v1.json`, or
pass `study_config=`. The approved owner registry/mask are external inputs,
verified before acquisition; there is no implicit workspace path or generated membership.

```python
from meteorology import get_weather, ALLOWED_VARIABLES, check_weather

result = get_weather("2009-01-01", "2009-01-02", ALLOWED_VARIABLES,
    study_config="/path/to/config/study.v1.json",
    output_dir="scratch/weather", cache_dir="/path/to/source-cache")
check_weather(result.catalog_path, study_config="/path/to/config/study.v1.json")
```

Allowed families: `temperature`, `dewpoint`, `relative_humidity`, `wind`,
`cloud_cover`, `pressure`, `precipitation`. Omit the list for all families.
Unknown, duplicate and empty lists reject before source access. Only selected
source parameters are fetched. Dates are inclusive UTC dates inside the approved
study. Output is the exact registry, without an intermediate rectangle export.

## Files and quantities

- `meteorology-hourly.parquet`: H3_INDEX string, DATETIME timestamp(us, UTC), selected float64 metrics.
- `meteorology-daily.parquet`: H3_INDEX string, DATE date32, selected float64 metrics.
- `catalog.json`: method, source metadata/evidence identities, units, dates, hashes and value-status totals.

All families yield 10 hourly and 14 daily columns. Each call replaces its selected
output range and columns, without appending. Unrelated output-directory files
cause an error. The returned result holds hourly_path, daily_path and catalog_path.

| Family | Hourly values | Daily reductions |
| --- | --- | --- |
| temperature | 2 m temperature, °C | mean, minimum, maximum |
| dewpoint | 2 m dewpoint, °C | mean |
| relative_humidity | derived 2 m RH, % | mean with all 24 supported hours |
| wind | 10 m U/V and speed, m/s; FROM direction, degrees | U/V means, speed mean/max, direction of mean vector |
| cloud_cover | total cloud cover, % | mean |
| pressure | mean sea-level pressure, hPa | mean, minimum |
| precipitation | following one-hour accumulation, mm | sum of 24 intervals |

Temperature/dewpoint convert Kelvin to Celsius, pressure Pa to hPa, cloud fraction
to percent, rainfall metres to mm. RH uses the Bolton-water derivation and retains
only finite 0–100.01% values with dewpoint at least −123.15°C; unsupported values
stay null. Calm wind direction is undefined at speed ≤1e-12 m/s. Daily direction
uses mean U/V; speed uses hourly magnitude. Min/max are extrema of hourly samples,
not within-hour extremes or gusts. Nearest quarter-degree source sampling adds no
independent H3-scale weather information (effective resolution approximately 31 km).

Hourly DATETIME is UTC hour start. Instantaneous fields use that timestamp;
rainfall is the interval following it, read at the interval-end timestamp.
Daily rainfall therefore uses 01:00 through next midnight; other fields use
00:00–23:00. Daily values require complete support. Nulls are never filled with zero.

## Source, cache and cutoff

Source: ECMWF geographically chunked ERA5 single-level Zarr, attributed to
Copernicus Climate Change Service / ECMWF, CC BY 4.0. See the
[ECMWF ARCO guide](https://confluence.ecmwf.int/pages/viewpage.action?pageId=505390919).
Credentials resolve from CDSAPI_KEY, CDSAPI_RC or ~/.cdsapirc. Tokens go only to the
fixed official HTTPS endpoint; redirects and proxy forwarding are disabled.
A complete retained-cache replay requires no token or provider request.

Defaults: output_dir="data/meteorology", sibling private .meteorology-cache,
workers=4 (allowed 1–4), transfer_bytes=8 GiB, max_seconds=3600 (up to 86400).
The Python API accepts these keyword options. Objects/arrays are checksummed;
metadata pins one source vintage. Rerun a failed call to reuse completed objects.
No automatic transient retry or eviction policy is implemented. A fresh cache
intentionally discovers a newer vintage, which can revise historical values.
Source/output paths must be separate. Publication is recoverable across its three
files; readers should verify catalog hashes and retry during replacement.

Dates beyond cached source support fail. Default rainfall needs next midnight.
`allow_incomplete_final_day=True` permits only the missing final boundary hour:
the retained October 10, 2026 source has 23 rainfall hours, null daily rainfall and
null hourly rainfall at 23:00. October 9 is the last complete source day for all
fields. Instantaneous-only requests do not need next midnight. Dates are not
silently shortened. ERA5/ERA5T remain retrospective; revision and historical
operational availability are unknown, and no forecast qualification is implied.

## Checks and CLI

```sh
meteorology get 2009-01-01 2009-01-02 --variables temperature wind --study-config /path/to/config/study.v1.json --output-dir scratch/weather --cache-dir /path/to/source-cache
meteorology check scratch/weather/catalog.json --study-config /path/to/config/study.v1.json
```

`python -m meteorology` and `python -m meteorology.regional` expose the same commands.
`get` also accepts --allow-incomplete-final-day; it has no dry-run option.
The Python API exposes additional resource caps. `check_weather` streams one day
at a time to verify hashes, schema/types/units, exact UTC lattice and uniqueness,
member consistency, finite/null representation and status totals. Pass study_config
to revalidate the external registry/mask; otherwise membership_verified is false.
`validate_product` remains a small alias for this weather contract. These checks
are structural/source consistency checks, not independent observational truth.

::: meteorology.regional.get_weather
    options:
      show_source: false

::: meteorology.regional.check_weather
    options:
      show_source: false
