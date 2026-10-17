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
`cloud_cover`, `pressure`, `precipitation`. Omit the list to use configured METRICS, or all families when unset.
Unknown, duplicate and empty lists reject before source access. Only selected
source parameters are fetched. Dates are inclusive UTC dates inside the approved
study. Output is the exact registry, without an intermediate rectangle export.

## Local run defaults

Copy the repository's `.env.example` to `.env` and set `DATA_DIR`, `START_DATE`,
`END_DATE`, `METRICS`, `VERBOSITY=True` or `False`, and `REBUILD=True` or `False`. The API and `get` CLI read `.env` in
the current working directory only, including when installed outside the checkout.
Use `env_file="/path/to/.env"` or `--env-file PATH` to select another file.
No file is read and no acquisition occurs on import. Only these six run keys
are read; values are never executed, expanded as shell expressions, or copied to
the process environment. Quote values containing spaces. Unknown keys are ignored.

Precedence is explicit API/CLI option, process environment, then `.env`. Dates
may be omitted individually if supplied by configuration. For file-free Python API calls,
dates remain required, output defaults to `data/meteorology`, verbosity is false, all metrics are selected, and rebuild is false. Relative file-based `DATA_DIR` resolves beside that file; explicit and
process-environment paths resolve from the working directory. Absolute paths work
in all cases. Personal `.env` files are ignored and excluded from distributions;
`.env.example` is the committed template. Study and credentials resolve separately.

```sh
meteorology get --cache-dir /path/to/retained-cache
meteorology get 2009-01-01 2009-01-02 --output-dir scratch/weather --no-verbose
```

```python
get_weather()  # dates/output/verbosity/metrics/rebuild from local settings
get_weather(env_file="/path/to/.env", verbosity=False)
```

Verbose runs report source/cache opening, each UTC processing day and successful
publication to stderr. CLI stdout retains the resulting catalog path. These
settings do not enable the incomplete-final-day option: explicitly pass
`--allow-incomplete-final-day` / `allow_incomplete_final_day=True` when qualifying
that boundary. They do not select historical source epochs.
A fresh source vintage may revise values; use scratch outputs for verification.

## Preflight

`meteorology preflight` (or `meteorology preflight --env-file PATH`) validates run
settings only and prints the six resolved values. It performs no study/credential
loading, directory creation, locking, source access, or publication. The same
`meteorology.run_settings.preflight` function runs automatically before `get`
starts work. Its checks cover:

- All six effective settings present and nonempty for CLI/file-backed runs.
- Valid ISO calendar dates with START_DATE <= END_DATE.
- A nonempty, unique list of allowed metric families.
- True/False verbosity and rebuild flags.
- A usable DATA_DIR, with an existing directory ancestor that is writable/searchable.
- Readable UTF-8 settings, well-formed assignments and no repeated known keys.

Errors are collected into one message naming keys and line numbers, without
printing raw values or unrelated credentials. Overrides retain the documented
precedence. Explicit Python API calls with no `.env` retain their documented
optional defaults, which are validated by the same function; a present/selected
file must supply any values not explicitly provided. CLI get requires all six
settings even without a file. The preflight is a local configuration check, not
proof of future source availability, credentials, free-space sufficiency or
scientific quality. Study gates, existing-product validation, actual source cutoff,
and publication-time storage checks still run at their corresponding stages.
Filesystem access is checked without probing writes and can change after preflight.

```sh
meteorology preflight
```

## Files and quantities

- `meteorology-hourly.parquet`: H3_INDEX string, DATETIME timestamp(us, UTC), selected float64 metrics.
- `meteorology-daily.parquet`: H3_INDEX string, DATE date32, selected float64 metrics.
- `catalog.json`: method, source metadata/evidence identities, units, dates, hashes and value-status totals.

All families yield 10 hourly and 14 daily metric columns. The returned result holds
hourly_path, daily_path and catalog_path. Unrelated output-directory files cause
an error in either mode.

## Rebuild and append modes

`METRICS` is a comma-separated list of the allowed family names above; the example
file comments list them. Explicit `variable_list` / `--variables` overrides it.
`rebuild=True` / `--rebuild` / `REBUILD=True` replaces existing weather files with
exactly the requested dates and metrics. `rebuild=False` / `--no-rebuild` /
`REBUILD=False` (the default) preserves existing dates and acquires only missing
dates and new metrics across saved dates. Both modes build normally if the destination is absent or empty.

Append inspects a complete three-file product and validates hashes, schemas,
units, UTC keys, membership and status/null totals before acquisition. Requests
with all dates and selected metrics already present return unchanged paths with
zero acquisition and no publication. Otherwise the final range is the union of saved and requested
dates. Only uncovered dates and newly requested metric dependencies trigger cube queries.
For disjoint ranges, the intervening dates are also fetched to retain the complete
UTC day lattice. Invalid existing files are not guessed around: use explicit
rebuild to replace a corrupt/incomplete weather product after inspection. Unrelated
files and symlink entries remain errors in both modes.

Append requires the same approved membership/envelope. Newly requested metrics
are acquired across the entire saved date range, including dates outside a narrower
new request. Old metric columns remain present even if omitted from the new list;
new dates include their values too. Saved columns are never reacquired or replaced
except when their native fields are dependencies of a new derived metric. Any such
retrieved dependencies are used for the new metric only; saved values remain
unchanged. Added columns align by exact cell/time keys. Metric-scoped source epochs
record that new metrics may come from a different source vintage. Existing valid
nulls, including a retained partial rainfall day,
are not missing dates and are preserved. Filling/revising such values requires an
explicit rebuild. The incomplete-final-day option still governs newly acquired
terminal support; it is not enabled by either rebuild setting.

Old values and null/status totals are retained; `source_epochs` preserves their
vintages separately from new intervals. No single source snapshot hash describes
a combined product. Because the deliverable is two monolithic Parquets, append
streams old row groups and new days into staged replacement files, requiring
space for the combined output; it does not redownload saved days or load the full
history into memory. A shared output lock covers inspection through publication,
and interrupted publication uses the existing recovery journal. Source-cache
snapshots stay pinned: a stale frontier still requires an explicitly selected
fresh cache to qualify newer source coverage.

```sh
meteorology get --no-rebuild
meteorology get 2009-01-01 2009-01-02 --rebuild --output-dir scratch/weather
```


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

Without run settings: output_dir="data/meteorology", sibling private .meteorology-cache,
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
meteorology get 2009-01-01 2009-01-02 --variables temperature wind --study-config /path/to/config/study.v1.json --output-dir scratch/weather --cache-dir /path/to/source-cache --no-verbose --no-rebuild
meteorology check scratch/weather/catalog.json --study-config /path/to/config/study.v1.json
```

`python -m meteorology` and `python -m meteorology.regional` expose the same commands.
`get` also accepts --allow-incomplete-final-day, --env-file, --verbose/--no-verbose, and --rebuild/--no-rebuild;
it has no dry-run option.
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
