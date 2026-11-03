# Solar Cycle

Solar Cycle is a distinct calculated product, authorized on 2026-11-01. It
describes daily solar geometry and light phases, not the Sun's 11-year activity
cycle. It is independent of ERA5 acquisition and shares local run configuration.

## Run

```python
from meteorology import get_solar_cycle, check_solar_cycle
result = get_solar_cycle(study_config="/path/to/config/study.v1.json")
check_solar_cycle(result.catalog_path, study_config="/path/to/config/study.v1.json")
```

```sh
meteorology solar --study-config /path/to/config/study.v1.json --verbose
meteorology solar 2024-06-20 2024-06-21 --study-config /path/to/config/study.v1.json --output-dir /path/to/scratch/solar-cycle
meteorology solar-check /path/to/Data/Meteorology/solar-cycle/catalog.json --study-config /path/to/config/study.v1.json
```

Dates are inclusive UTC dates. Without configured dates, defaults select the entire approved study:
currently 2009-01-01 through 2026-12-31. Future astronomical dates are calculated;
they are not weather forecasts. No credentials, network, external ephemeris or
additional runtime dependency is required. Shared [.env settings](regional-api.md#local-run-defaults) supply dates, bounds,
output root and rebuild mode.

Default output is `Meteorology/solar-cycle/` beneath the selected study's Data
root. `output_dir` selects a dedicated alternative. A covered request validates
and preserves the saved bytes. Append extends the continuous union of saved and requested dates,
computing missing days and retaining saved rows. Changing the region or calculation
code requires explicit `rebuild=True` / `--rebuild`; rebuild replaces the requested
range through recoverable publication. Unknown files, symlinks, damaged products, changed registry
membership and changed method conventions fail closed.

## Files, support and quantities

| File | Key | Values |
| --- | --- | --- |
| `solar-cycle-hourly.parquet` | `H3_INDEX`, `DATETIME` (UTC) | `SOLAR_ELEVATION_DEG`, `SOLAR_AZIMUTH_DEG`, `SOLAR_PHASE` |
| `solar-cycle-daily.parquet` | `H3_INDEX`, `DATE` (UTC) | Sunrise/sunset; civil, nautical and astronomical dawn/dusk UTC timestamps; five phase durations in hours |
| `catalog.json` | Separate Solar Cycle catalog | Method, conventions, units, study/membership hashes, code/environment versions, file hashes and null counts |

Spatial support is the WGS84 centre of each exact approved coastal H3 R5 cell
(currently 463). This is point geometry, not a cell-area average, observer-location
coverage, or a finer representation of ERA5. The approved registry is external.

Hourly position is instantaneous at each exact UTC hour, not an hourly mean.
Elevation is the **geometric centre of the Sun**, degrees above the mathematical
horizon, without atmospheric refraction. Azimuth is degrees clockwise from true
north, `[0,360)`; at an exact zenith/nadir its undefined value is null.

Daily event columns are `SUNRISE_UTC`, `SUNSET_UTC`, `CIVIL_DAWN_UTC`,
`CIVIL_DUSK_UTC`, `NAUTICAL_DAWN_UTC`, `NAUTICAL_DUSK_UTC`,
`ASTRONOMICAL_DAWN_UTC`, and `ASTRONOMICAL_DUSK_UTC`.

| Exclusive phase | Geometric solar-centre elevation | Daily duration |
| --- | --- | --- |
| Daylight | At least −50/60° | `DAYLIGHT_HOURS` |
| Civil twilight | At least −6°, below −50/60° | `CIVIL_TWILIGHT_HOURS` |
| Nautical twilight | At least −12°, below −6° | `NAUTICAL_TWILIGHT_HOURS` |
| Astronomical twilight | At least −18°, below −12° | `ASTRONOMICAL_TWILIGHT_HOURS` |
| Night | Below −18° | `NIGHT_HOURS` |

The −50/60° rise/set convention accounts approximately for solar radius and
standard horizon refraction. Hourly angles remain geometric. Dawn is a rising
crossing and dusk a setting crossing.

Every event belongs to the named **UTC** date. On this coast sunset commonly
precedes sunrise within that date. Durations integrate all segments inside
`[00:00, next 00:00)` UTC and sum to 24 hours; do not subtract same-row sunset
minus sunrise to estimate day length. These are not local-calendar-day durations.
Null events mean no crossing on that UTC date, not absent observations. In summer,
astronomical dawn/dusk may be null and night duration genuinely zero.

## Method, provenance and limitations

`solar.py` implements NOAA/Meeus Julian-century solar-coordinate equations.
Daily extrema bracket threshold crossings, refined by 20 bisection steps (less
than 0.1 seconds of numerical interval uncertainty). This retains short night
intervals between hourly samples. Numerical precision does not imply real-world
sunrise accuracy.

Positions support UTC dates in 1900–2100 and latitudes within ±72°; production
additionally enforces the study window. A sea-level, unobstructed mathematical
horizon is assumed. Terrain, canopy, eye height, local pressure, refraction
variability, clouds, fog, radiation and observer bearing are not inputs. This is
astronomical context, not measured illumination, physical viewability, glare,
detection probability or species response.

[NOAA calculation details](https://gml.noaa.gov/grad/solcalc/calcdetails.html)
describe the equations and approximate rise/set accuracy; NOAA's legacy calculator
is no longer maintained. [USNO definitions](https://aa.usno.navy.mil/faq/RST_defs)
specify the conventions and atmospheric/horizon limitations. No astronomical
observation archive is downloaded or implied.

Each UTC day is one Parquet row group; memory is bounded to one day. Code hashes,
library versions, registry/mask identity and file checksums are retained. Staging
space is checked before production and free space periodically thereafter.
The transactional publisher stages and journals under
`Data/Meteorology/tmp/solar-cycle-<destination-hash>`. Both files and their
terminal catalog are validated before promotion, then read back. Weather recognizes
this complete companion and the separate [Solar Radiation](solar-radiation.md)
directory beside its existing three files.

## Validation scope

`tests/test_solar.py` covers frozen Astral 3.2 references, leap-day positions,
solstices, phase thresholds, short nights, split UTC days, absent crossings,
registry support, no-op/rebuild behavior, failed rebuild preservation and corrupted
delivery rejection. Astral is reference evidence only, not a runtime/test dependency;
both implementations use NOAA/Meeus, so this is not observational validation.

The catalog and workspace `Reports/solar-cycle-2026-11-01/summary.json` record
actual production checks. `check_solar_cycle` checks hashes, schemas, metadata,
exact keys/membership, null counts, angle/phase validity, event dates and the
24-hour duration partition. [Solar Radiation](solar-radiation.md) is separately
implemented; waves remain an [investigated addition](radiation-and-waves.md).

### Delivered and checked 2026-11-01

The 463-cell 2009-01-01–2026-12-31 product contains 73,050,288 hourly and
3,043,762 daily rows, about 1.57 GiB total. Full staged and final readbacks passed.
All hourly angle/phase values and daily durations are populated. Each astronomical
dawn/dusk column has 220,141 nulls for dates without that threshold crossing;
these are not missing source records. Existing weather files and catalog have
unchanged SHA256 hashes.

On Python 3.12.13, all 157 offline tests passed from source and again against the
regular installed wheel outside the checkout. Six complete days (first date,
leap day, both solstices, weather endpoint and final future date) reproduced all
delivered values exactly from that wheel. The installed CLI created a separate
two-day scratch product. Ruff, strict MkDocs, wheel/sdist builds, Twine, dependency
checks and whitespace checks passed. No Python 3.11/3.13 environment run,
independent astronomical observation comparison, illumination/detection validation,
or radiation/wave acquisition was performed. The compact workspace production
record retains exact hashes, environment and commands; no bulk source archive
or duplicate full product was created.
