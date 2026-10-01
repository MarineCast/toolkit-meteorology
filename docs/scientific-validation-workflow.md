# Offline scientific comparison workflow

`meteorology validate-science` compares frozen producer outputs with independently supplied,
checksum-verified reference files. It makes no network request and never changes a release.
This workflow implements the software gate in the [validation plan](scientific-validation-plan.md).
A run against synthetic inputs proves the comparison software works; it is **not** a station,
buoy, GRIB, or ephemeris accuracy result.

## Inputs and grain

Use `meteorology freeze-release` for the weather, daylight, and lunar manifests before running
the comparison. The weather freeze contains the acquisition inventory and referenced R5
timestamp samples. A run can select any nonempty subset of the three families. Each selected
manifest is deeply validated and must have an immutable release directory.

The reference bundle is a JSON file with `schema_version: 1`, `evidence_class`
(`synthetic_fixture` or `independent_reference`), a `sites` file specification,
and optional `weather` and `astronomy` file specifications. Every file specification has a
relative `path` and a SHA-256 of the exact file bytes. Weather and astronomy specifications
also have a `source` object: `name`, `uri`, `retrieved_at_utc`, `license`, `attribution`,
`redistribution_restrictions`, and nullable `may_share_assimilation_inputs`. The latter is
`null` when independence has not been established; it must not be guessed from provider names.
The bundle and every input file are hashed again in the report. Bundle paths cannot escape its
directory. Keep provider files and any redistribution permission evidence separately.
`independent_reference` is a source declaration, not a software certification of independence;
the result gate remains `REFERENCE_COMPARISON_UNREVIEWED` pending source review.

`sites.csv` has one row per `site_id`: `latitude`, `longitude`, `elevation_m`, `region`,
`platform`, `wind_height_m`, `wind_basis`, `source_site_uri`. The measurement CSV has one row
per `record_id`: `site_id`, `valid_time_utc`, `metric`, `value`, `unit`, `definition`,
`quality_flag`, `height_m`, `interval_start_utc`, `interval_end_utc`. All timestamps carry a UTC
offset. Supported point metrics and required units/definitions are:

| Metric | Unit | Required definition | Match |
| --- | --- | --- | --- |
| `TEMPERATURE_2M_C` | `degC` | `air_temperature_2m` | Instantaneous `sfc/f00`; sensor height 2 m |
| `RELATIVE_HUMIDITY_2M_PCT` | `percent` | `relative_humidity_2m` | Instantaneous `sfc/f00`; sensor height 2 m |
| `U_WIND_10M_MS`, `V_WIND_10M_MS` | `m/s` | `earth_relative_wind_10m` | Instantaneous `sfc/f00`; both components and 10 m height required for derived speed/direction |
| `MEAN_SEA_LEVEL_PRESSURE_HPA` | `hPa` | `sea_level_reduced_pressure` | Instantaneous `sfc/f00`; site elevation retained |
| `VISIBILITY_KM` | `km` | `horizontal_visibility_surface` | Instantaneous surface visibility; interpretation remains point-vs-grid context |
| `TOTAL_CLOUD_COVER_PCT` | `percent` | `total_column_cloud_cover` | Only a genuinely comparable total-column reference; ordinary sky-cover codes do not qualify |
| `PRECIP_4H_MM` | `mm` | `gauge_4h_accumulation` | **Sampling-sensitivity diagnostic:** reference interval must be exactly `[valid_time-4h, valid_time]`; model value is `f01 PRATE × 4h`, not a four-hour forecast accumulation |

Unsupported definitions, units, interval types, wind bases, heights, missing or nonfinite
values, failed quality flags, unmatched times, distant sites, unavailable H3 cells, and missing
components are written to the exclusion table with explicit reasons. No value is zero-filled.
Sites are mapped to their R5 H3 cell; the site-to-centroid offset and published nearest-grid
offset are retained. A site measurement is a point comparison to a nearby model sample, not an
H3 area average. The protocol's time and distance tolerances are applied before pairing.
Wind direction uses the earth-relative U/V vectors and is excluded when either vector is calm
under the prespecified speed threshold.

`astronomy.csv` has one row per `record_id`: `h3_index`, `date`, `valid_time_utc` (required only
for instantaneous altitude), `metric`, `value`, `unit`, `latitude`, `longitude`, `quality_flag`,
`case_tag`. Supported metrics are `DAYLIGHT_HOURS`, `SOLAR_ELEVATION_MAX_DEG`,
`SOLAR_ALTITUDE_DEG_AT_UTC`, `LUNAR_ILLUMINATION_FRACTION`, `LUNAR_PHASE_ANGLE_DEG`,
`MOON_ALTITUDE_DEG_AT_UTC`, and `MOON_VISIBLE_DARK_HOURS`. Daily values come from the frozen
R4 daylight or R5 lunar release. Instantaneous solar/lunar altitude invokes the same production
function used by the builders at the recorded UTC instant; the report marks these pairs as
function evaluations, not frozen daily values. Reference coordinates must match the H3 centroid
within the protocol tolerance. The bundle's `astronomy_conventions` must declare geometric
horizon, no refraction, the release timezone, and the relevant sample hour, time steps and
sun/moon thresholds. Incompatible conventions are excluded rather than compared. Reference
values must be computed by an independent implementation or retained independent ephemeris.
The JSON keys are `horizon: "geometric"`, `refraction: "none"`, plus `daylight` and/or `lunar`
objects. The `daylight` object exactly matches that release's `timezone`, `timestep_minutes`,
`low_sun_max_degrees` and `leap_day_policy`; `lunar` exactly matches `timezone`,
`timestep_minutes`, `sample_hour_utc`, `dark_sun_altitude_deg` and `moon_altitude_min_deg`.
Include objects only for selected astronomy releases.
The `case_tag` is source-declared; the report lists represented and absent equinox, solstice,
leap-day, polar, spring-DST and fall-DST cases. Labels alone are not accuracy evidence.

## Prespecified protocol and outputs

The protocol JSON is a separate file with `schema_version: 1`, `name`, `locked_at_utc`,
`rationale`, `holdout` (`site_ids`, `start_utc`, `end_utc`, `astronomy_case_tags`),
`pairing` (`max_time_delta_minutes`, `max_centroid_distance_m`,
`max_source_distance_m`, `max_astronomy_coordinate_offset_m`,
`height_tolerance_m`, `calm_speed_threshold_ms`, `wet_threshold_mm`,
`precip_intensity_edges_mm`),
`accepted_quality_flags`, and `thresholds`. Each metric threshold specifies `min_pairs`,
`max_abs_bias`, `max_mae`, `max_rmse`, and a scientific `rationale`. The file hash, lock time,
holdout and thresholds are recorded before metrics are computed. This workflow cannot prove
the protocol was selected before its author saw data; retain review history externally.
No built-in accuracy threshold is invented. Missing thresholds prevent the run.

```sh
meteorology validate-science \
  --weather-manifest /path/to/frozen-weather/MANIFEST.json \
  --daylight-manifest /path/to/frozen-daylight/MANIFEST.json \
  --lunar-manifest /path/to/frozen-lunar/MANIFEST.json \
  --reference-bundle /path/to/reference-bundle.json \
  --protocol /path/to/locked-protocol.json \
  --output-dir /path/to/new-validation-run
```

The command refuses an existing output directory. It writes `paired.parquet`,
`excluded.parquet`, `results.json`, and `REPORT.md` as one staged directory. Paired records
retain model/reference values, signed errors, units, UTC time, H3/site identity, offsets,
source mode, region, season and case tag. The JSON and Markdown report include release IDs,
method IDs, producer SHA/source hash, reference file hashes and rights, package environment,
pair/exclusion counts, bias/MAE/RMSE, region/season strata, wind circular errors,
precipitation occurrence/intensity diagnostics, threshold decisions, and scope limitations.
Empty strata are `NOT_EVALUATED`, not zero error. The four-hour precipitation comparison is
always labelled `SAMPLING_SENSITIVITY`, even if its numeric threshold passes.
The JSON has exact input reference counts, per-metric candidate, eligible, paired and excluded
counts, and an exclusion `stage` (`eligibility` or `pairing`). Eligible means the row passed
holdout, QC, unit and definition checks before spatial/temporal matching; derived wind metrics
have their own candidate counts. The threshold gate is `NOT_EVALUATED` when a required
non-precipitation metric has too few pairs, and `FAIL` if one breaches its prespecified
tolerance. A declared `synthetic_fixture` can yield only `SOFTWARE_ONLY` evidence.

## Live evidence boundary

This command does not acquire HRRR or reference data. For a bounded provider pilot, first run
`meteorology download surface-weather --start-date DATE --end-date DATE --dry-run` in a
disposable workspace, record the six expected valid times and region, then explicitly enable
only that one-day network request. Freeze all outputs, record actual/logical GRIB URIs and
retrieval times, decoded fields and decoder versions, plus cryptographic hashes of the exact
retained subset or whole GRIB object. A URL suffix or ETag is not a verified byte hash.
Use separately acquired, rights-checked station/buoy and ephemeris references. A one-day
provider smoke cannot establish seasonal, regional or prospective forecast accuracy.

The packaged example area's reproducible preview command is:

```sh
meteorology --workspace /path/to/fresh-disposable-workspace init
meteorology --workspace /path/to/fresh-disposable-workspace download surface-weather \
  --start-date 2024-01-02 --end-date 2024-01-02 --dry-run
```

On the packaged 2026-10-01 configuration, this reports six R5 valid times for the example
bounding box 46.85–50.00 N, 125.80–121.60 W. It makes no network request; actual acquisition,
GRIB decoding and reference comparison remain separate evidence steps.
