# Solar Radiation

Solar Radiation is a separate coastal R5 ERA5 product authorized on 2026-11-01.
It delivers source downward shortwave (`ssrd`) and direct solar (`fdir`) radiation,
independent of [Weather](regional-api.md) and calculated [Solar Cycle](solar-cycle.md).

## Run and actual source coverage

```python
from meteorology import get_solar_radiation, check_solar_radiation

result = get_solar_radiation("2009-01-01", study_config="/path/to/config/study.v1.json")
check_solar_radiation(result.catalog_path, study_config="/path/to/config/study.v1.json")
```

```sh
meteorology radiation 2009-01-01 --study-config /path/to/config/study.v1.json --verbose
meteorology radiation 2009-01-01 --study-config /path/to/config/study.v1.json --allow-incomplete-final-day
meteorology radiation-check /path/to/Data/Meteorology/solar-radiation/catalog.json --study-config /path/to/config/study.v1.json
```

The default output is `Data/Meteorology/solar-radiation/`, relative to the selected
study's Data root. `output_dir` chooses a dedicated alternative. Dates are inclusive
UTC dates inside the approved study. Omitted start uses the study start; omitted
end inspects the actual pinned source time coordinates and selects the latest
complete UTC day. These date defaults apply only when dates are not configured. Shared
[.env settings](regional-api.md#local-run-defaults) supply dates, output root,
bounds and mode. METRICS controls Weather only; Radiation publishes all its fields.

The live 2026-11-01 inspection found source coordinates ending **2026-10-26
23:00 UTC** for both fields. Complete daily energy is supported through October
25. The explicit `allow_incomplete_final_day=True` / CLI flag retains October 26
with its missing 23:00–00:00 interval null, and null daily totals/means. This option
accepts exactly one missing terminal hour, never a longer boundary gap. It is not
enabled implicitly. Source coverage is not calculated from an assumed calendar lag.

`workers` is 1–4, `transfer_bytes` bounds downloads (default 8 GiB), and
`max_seconds` bounds source/processing work (default 3,600; at most 86,400 seconds).
Credentials use the existing redacted official ECMWF resolver. No automatic API
retry is implemented; rerunning reuses verified completed cache objects/arrays.

## Files, keys, units and timing

| File | Key | Quantities |
| --- | --- | --- |
| `solar-radiation-hourly.parquet` | `H3_INDEX`, `DATETIME` (UTC) | Following-hour energy in J m⁻² and hourly mean irradiance in W m⁻² for each field |
| `solar-radiation-daily.parquet` | `H3_INDEX`, `DATE` (UTC) | Complete UTC-day energy in J m⁻² and full-day mean irradiance in W m⁻² |
| `catalog.json` | Separate Solar Radiation catalog | Units, conventions, source coordinates/receipts, vintage epochs, hashes, null counts and quality evidence |

Hourly columns are `DOWNWARD_SHORTWAVE_ENERGY_J_M2`,
`DOWNWARD_SHORTWAVE_IRRADIANCE_W_M2`, `DIRECT_SOLAR_ENERGY_J_M2`, and
`DIRECT_SOLAR_IRRADIANCE_W_M2`. Daily energy columns have the same names but cover
the complete UTC day; daily irradiance columns add `_MEAN`.

Both source fields are one-hour **accumulated energy**, stamped at interval end.
They are not running cumulative totals and must not be differenced. A public row
at 00:00 describes `[00:00,01:00)` and reads source 01:00. Daily energy sums source
interval ends 01:00 through next 00:00. Hourly irradiance is energy / 3,600 seconds;
daily mean irradiance is complete daily energy / 86,400 seconds, including night.

Both quantities refer to a **horizontal plane**, downward positive. SSRD includes
direct and diffuse shortwave radiation; FDIR is the model direct horizontal
component. It is not direct-normal irradiance, sunshine duration, PAR, a spectral
measurement, glare or observer illumination. No solar-angle division or empirical
direct/diffuse estimation is performed. FDIR retains the model's scattering convention.

## Spatial support and missingness

The product uses exact approved coastal R5 membership (463 cells currently), sampled
at each cell centre's nearest verified native 0.25° point. The retained crosswalk
records native coordinates/distances and distinct source points. This adds no
independent detail to the approximately 31 km atmosphere and is not a water-area
average or a correction for terrain, canopy, observer bearing or local shadow.

Every day has a complete key lattice: 24 hourly rows and one daily row per cell.
Nonfinite source values become Arrow nulls. Real source zeros and finite signed
values remain unchanged; no night zero-filling, clipping, smoothing or interpolation
is applied. Each daily quantity independently requires 24 finite source intervals.
A missing SSRD interval does not erase otherwise supported FDIR values.

The quality record lists partial days, counts negative finite source energies, and
counts FDIR values exceeding SSRD by more than 10⁻⁶ J m⁻². Such values are preserved
as source evidence, not silently adjusted. These diagnostics do not establish
observational accuracy or ecological eligibility.

## Append, rebuild, source epochs and storage

Default append validates saved bytes, exact membership, units, provenance and all
hourly/daily arithmetic. It preserves retained values, including nulls, and acquires
only new dates. Prepend/tail/disjoint requests produce one continuous union range;
bridges are explicitly filled. Source evidence and nonoverlapping date epochs
retain each acquisition vintage. An explicit covered-date request performs no
source acquisition or publication. Omitted end may make a bounded source-coverage
probe before concluding that no new dates are available.

An old partial endpoint stays null during later append. Replacing it or revising
any retained source value requires explicit `rebuild=True` / `--rebuild`, which
replaces the selected range. This is not an automatic revision/repair mechanism.
Changed producer/source/reduction code also requires explicit rebuild; unknown
files, damaged products and ownership mismatches fail closed.

Staging, source payloads, locks and journals are under
`Data/Meteorology/tmp/solar-radiation-<destination-hash>`. An optional `cache_dir`
is a work base with an owned destination-specific child; unowned files are not
adopted or swept. Disk preflight, object/array limits and periodic headroom checks
bound growth. Successful full final readback removes owned compressed objects,
decoded arrays and mapping cache; compact source receipts, field metadata and
crosswalk remain in the catalog. Failed runs retain resumable sources. Source
metadata is pinned during a run; a resumed cache can have an older frontier.

Both Parquets and terminal catalog publish through the existing recoverable
transaction. Weather recognizes the complete named `solar-radiation` companion
alongside `solar-cycle`. Existing Weather and Solar Cycle files are preserved.
Readers should validate catalog hashes and retry if they encounter a concurrent
multi-file promotion.

## Source and verification

- [ECMWF ARCO guide](https://confluence.ecmwf.int/pages/viewpage.action?pageId=505390919): fields, grids and accumulation time-coordinate conventions.
- [ERA5 documentation](https://confluence.ecmwf.int/pages/viewpage.action?pageId=76414402): preceding-hour accumulations and midnight support.
- [FDIR definition](https://codes.ecmwf.int/grib/param-db/228021): direct horizontal energy and flux conversion.
- [IFS radiation documentation](https://www.ecmwf.int/sites/default/files/2023-06/Part-IV-Physical-Processes.pdf): horizontal versus normal planes and model scattering treatment.

`regional_source.Archive` now accepts an explicit bounded native-field contract;
Weather's default seven-field contract remains intact. Radiation verifies actual
units, layout, hourly coordinates, GRIB parameter IDs and accumulated step type
before processing. No sibling toolkit or application import is required.

`check_solar_radiation` streams both files together, verifies every key and all
energy-to-irradiance conversions, recomputes every daily energy/mean from all
24 hourly values, verifies source-epoch partition/evidence hashes, and reconciles
quality/null totals. Before publication the writer also compares selected days'
written energies directly with native interval-end samples. Offline tests cover
boundaries, missingness, zero/signed values, append vintages, bridge/prepend,
no-op/rebuild, failures, cache ownership and Weather coexistence.

Actual production/check evidence is retained in workspace
`Reports/solar-radiation-2026-11-01/summary.json` and the separate catalog. ERA5/ERA5T
identity, forecast init/lead and historical operational availability remain unknown.
This is retrospective reanalysis, not a qualified forecast or independent radiometer
validation. Fresh acquisition may revise historical values; post-cleanup exact raw
source reconstruction is not promised.

### Delivered and checked 2026-11-01

The delivered 2009-01-01–2026-10-26 product has 72,316,896 hourly rows and
3,013,204 daily rows across all 463 cells, sampling 243 distinct native points.
The two Parquets total 548,021,226 bytes (about 523 MiB). Each metric has exactly
463 hourly nulls and 463 daily nulls, all at the documented October 26 endpoint.
There are no other source gaps, negative energies or FDIR-above-SSRD diagnostic
cases in this delivered snapshot.

All keys, hashes, units, source epochs and every energy/irradiance/daily reduction
passed staged and final readback, then passed the installed wheel's CLI checker
outside the checkout. Three dates (2009-01-01, 2012-02-29 and 2026-10-26) matched
native source energies exactly. The 526 source requests transferred 529,364,177
bytes including metadata/coordinates; production reused those verified blocks.
Successful source payloads were removed after final readback. The existing
Weather and Solar Cycle files/catalogs retain identical SHA256 hashes.

On Python 3.12.13, 173 offline tests passed from source and again against the
regular installed wheel. Ruff, strict MkDocs, wheel/sdist builds, Twine,
dependency and whitespace checks passed. Other Python environments, independent
radiometer accuracy, physical illumination/detection and ecological model
qualification were not tested. No wave product or application integration was added.
