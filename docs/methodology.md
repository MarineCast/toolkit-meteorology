# Scientific methodology

## Product and time identities

Weather is a retrospective model-derived context product. Core fields come from the NOAA/NCEP HRRR `sfc/f00` analysis at six local wall-clock sample times, four hours apart. The precipitation field comes from `sfc/f01` PRATE initialized one UTC hour earlier and valid at the same sample time. The acquisition inventory keeps `INIT_TIME_UTC`, `VALID_TIME_UTC`, `PRECIP_INIT_TIME_UTC`, `PRECIP_FORECAST_HOUR`, logical object identity, actual retrieval URI/time, grid hash and retained sample checksum distinct. The logical identity denotes the expected NOAA object; retrieval URI/time record the endpoint and time actually used. Raw GRIB bytes are not retained or checksummed, so the sample checksum is evidence for the derived R5 artifact rather than an independent raw-object fingerprint. The daily row stores first/last valid times and latest declared availability time. NOAA describes the [HRRR](https://rapidrefresh.noaa.gov/hrrr/) as an hourly updated high-resolution numerical weather model; model values are not station observations.

`DATE` is the configured IANA timezone's civil date. The product selects local wall-clock hours 00, 04, 08, 12, 16 and 20. Those six instants are unique on ordinary and tested DST days; adjacent UTC gaps can be three or five hours at a DST transition. The `latest_complete` end date uses previous-local-calendar-day arithmetic and the final sample's declared availability boundary. The precipitation estimate still applies a nominal four-hour multiplier to every sample and therefore must never be relabeled as an actual 23-, 24- or 25-hour accumulation. Solar/lunar integration samples the whole local civil day, yielding 23 or 25 hours on DST changes. Integrated-hour validators use that row's actual civil-day duration; geometric `DAYLIGHT_HOURS` remains bounded by 24. One timezone is configured for the whole product; per-cell timezone assignment is not implemented.

## Spatial support and mapping

Atmospheric support contains H3 cell **centroids inside the configured WGS84 bounding box**, including land and water. R4 supports daylight, R5 supports weather and lunar products, and R6 is retained as an optional atmospheric support layer. R6 is not a resampled R5 weather product. These resolutions are fixed by configuration validation. The optional daily matrix preserves each native resolution as separate rows.

For each identified HRRR source grid, the producer finds the closest source-grid coordinate to each R5 H3 centroid with a haversine BallTree. It records source row index, grid hash and great-circle distance in a crosswalk. The sampled value is **nearest-neighbor point sampling**, not an area-weighted H3 mean. Mixed land/water cells and coastal microscale effects are not resolved by the H3 polygon. The decoded native grid-edge polygon must cover the requested box and every support centroid; the crop must contain nearby grid points at its corners. Nearest-grid distances must stay within an allowance derived from adjacent native grid spacing. Missing source pixels fail the strict acquisition path. This is a CONUS HRRR method and does not implement global or antimeridian handling.

The [GRIB wind-reference flag](https://confluence.ecmwf.int/spaces/UDOC/pages/212440350/What+are+Code+and+Flag+tables+-+ecCodes+GRIB+and+BUFR+FAQ) is checked for both 10 m components. If it identifies grid-relative vectors, local geodesic bearings of the decoded grid axes determine an orthonormal local basis before nearest-cell sampling. This preserves mixed-vector magnitude, including at tested grid boundaries. Source and published bases are retained in each sample; missing or conflicting flags fail acquisition. Synthetic analytic tests exercise direction, scan handedness, magnitude and no double rotation; a separate PROJ Lambert-convergence oracle checks multiple longitudes. An earlier one-cycle HRRR smoke at 2024-01-02 12:00 UTC was author-reported for the preceding revision. A retained real GRIB/projection reference and regional wind observations remain to be checked for this revision.

## Daily weather reduction

Every R5 cell and local date must have all six validated source samples. A missing, duplicated, non-finite or unsupported source value blocks publication of that family. The daily row publishes `EXPECTED_SAMPLE_COUNT=6`, `SAMPLE_COUNT=6`, `SAMPLE_COVERAGE_FRAC=1` and `QC_STATE=COMPLETE`. The current method does not publish partial-day values.

| Field family | Daily calculation |
| --- | --- |
| 2 m temperature, relative humidity, total cloud cover | Arithmetic mean of six values |
| Wind speed | Mean and maximum of six vector magnitudes |
| U/V wind | Arithmetic mean of each component; vector speed is `hypot(mean U, mean V)` |
| Wind direction | `atan2(-mean U, -mean V)` converted to degrees clockwise from north, **from** which the wind blows; null for a calm mean vector |
| Surface gust | Mean and maximum of six values |
| Visibility | Mean and minimum of six values |
| Mean sea-level pressure | Mean and minimum of six values |
| Source-grid distance | Mean and maximum nearest-source distance, as QC context |
| Precipitation | Sum of six `f01` PRATE values in mm/h, each multiplied by nominal 4 h |

The wind direction of 359° and 1° averages near north under the vector method. Mean wind speed and mean-vector speed answer different questions: opposing winds can have a positive speed mean and zero vector mean. Calm vector direction is undefined and remains null.

PRATE is a forecast rate in kg m⁻² s⁻¹, converted to mm/h by multiplying by 3600 for liquid-water equivalent. The six valid times are distinct, so the algorithm counts each rate once. It extrapolates each point rate across a nominal four-hour window; it does **not** use six measured disjoint accumulation windows, and no 24-hour precipitation total is claimed. A fully dry range can legitimately contain only zeros; missing or invalid source records still block publication. NOAA's [HRRR field documentation](https://rapidrefresh.noaa.gov/RAP_var_diagnosis.html) provides source-field context; the precise selectors and unit checks are in the acquisition inventory and [variable inventory](reference/variables.md).

## Solar context

Day length uses an approximate solar declination, `23.44° × sin(2π(solar_day−81)/365)`, and hour-angle geometry at the H3 centroid. Polar day/night clamp to 24/0 hours. February 29 maps to solar day 60, the same proxy as March 1. The compact lookup carries `MONTH_DAY`, `IS_LEAP_DAY` and `SOLAR_DAY_365` so a non-leap consumer need not join on the leap reference year's ordinal. Its selected weight keeps the daily method's value; it is not rescaled to the lookup maximum. The separate solar-altitude profile samples each local civil day at the configured timestep to calculate sampled maximum elevation, mean positive elevation, and low-sun hours. Mean positive elevation is null when there is no sampled daylight. The within-cell and global daylight weights use fixed annual normalization bounds; if a bound has zero range, the method falls back to the absolute daylight fraction instead of assigning full weight to darkness.

There is no terrain horizon, refraction, cloud attenuation or ephemeris-grade accuracy guarantee. `DAYLIGHT_HOURS` is a geometric approximation; it may differ from the timestep-sampled solar profile near thresholds.

## Lunar context

Lunar age uses a fixed reference new moon (2000-01-06 18:14 UTC) and a 29.530588853-day synodic cycle. Phase angle and illuminated disk fraction are deterministic functions of that age at the configured UTC sample hour. Moon altitude uses a low-precision orbital approximation. For each local-day timestep, the producer tests geometric moon altitude against the configured horizon and solar altitude against the configured darkness threshold, then sums visible-dark hours and illumination-weighted equivalent hours. A fraction of night is null when `NIGHT_HOURS=0`.

Lunar disk illumination is **not** surface illuminance. Clouds, atmospheric transmission, terrain, artificial lights and water reflections are not modeled. See [limitations](limitations.md) for deployment implications.

## Provenance and reproducibility

Each family manifest records schema version, software version, scientific method version, run ID, content-derived release ID, code source hash, resolved configuration/hash, spatial/temporal support, source attribution, input and output checksums, units, formulas and limitations. `run_id` identifies one execution; `release_id` identifies the content/configuration/method/software combination independent of the manifest location. Paths inside the resolved configuration can affect the release ID. Repeating an identical build can produce different Parquet bytes or timestamps, so compare the recorded artifact checksums when exact byte identity matters.

`meteorology validate --manifest ...` checks manifest integrity, file checksums, Arrow schemas, row counts, keys, H3 resolution, selected physical ranges and daily coverage. `meteorology freeze-release --manifest ... --output-root ...` copies a validated family into a new release-ID directory and refuses an existing target. For weather it also copies every sample and crosswalk addressed by the acquisition inventory. Retain the frozen directory and its checksum-backed manifest together. The routine family build paths remain mutable working outputs; freeze before rebuilding when prior outputs must be retained.

Current acquisition publication also requires the exact continuous local-date sample schedule and
revalidates every retained row's declared lag, provenance, sample and crosswalk. A new method ID
does not change an already generated release; regenerate affected data before claiming these
contracts for that release. For older method IDs, use the [pinned archived validation handoff](archived-validation.md).
