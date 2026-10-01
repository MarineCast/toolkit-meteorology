# Independent scientific validation plan

Code, synthetic fixtures, one bounded HRRR cycle, one buoy day and ten USNO
astronomy cases establish software behavior and preliminary point comparisons
for their tested conditions. They do not establish regional accuracy or
prospective ecological value.

## Bounded October 1, 2026 pilot evidence

The configured example box was narrowed to 46.85–49.70°N, 125.80–121.60°W
after the original northwest corner failed the decoded CONUS HRRR footprint
check. On 2024-01-02 08 UTC, nine f00 fields and f01 precipitation decoded from
NOAA's public archive. All 450 R5 centroids mapped to the cropped grid; maximum
nearest-point distance was 1.95 km under the decoded-grid allowance of 4.25 km.
The pilot retained source URIs and scalar samples, not full GRIB bytes.

The [USNO reference](https://github.com/MarineCast/toolkit-meteorology/blob/main/validation/references/usno_2024_solar_lunar.json)
contains full one-day service responses for eight Pacific Northwest dates and
two polar cases. The [comparison report](https://github.com/MarineCast/toolkit-meteorology/blob/main/validation/reports/usno_2024_solar_lunar.json)
records each error and its reference URL. Before calculating errors, the pilot
set daylight MAE/max limits of 0.5/1.0 hours and matched-local-noon lunar
illumination MAE/max limits of 0.10/0.20 fraction. Results were 0.209/0.338
hours and 0.029/0.083 fraction, respectively. USNO sunrise/set uses an apparent
horizon while the toolkit uses a geometric approximation; the toolkit's lunar
product samples 12 UTC, not USNO local noon. These comparisons are descriptive
method checks, not an ephemeris-grade certification.

The [JPL Horizons airless-altitude reference](https://github.com/MarineCast/toolkit-meteorology/blob/main/validation/references/jpl_horizons_2024_altitudes.json)
contains 144 hourly solar and 144 hourly lunar altitudes across four regional
and two polar UTC dates. Its [report](https://github.com/MarineCast/toolkit-meteorology/blob/main/validation/reports/jpl_horizons_2024_altitudes.json)
compares exact times and zero-height geodetic coordinates. Prespecified solar
MAE/max limits were 2°/5° and lunar limits 6°/12°; the measured solar
MAE/max were 0.003°/0.007° and lunar MAE/max were 0.775°/1.394°.
Hourly dark-and-moon-visible counts agreed for all six UTC days. The hourly
count is a diagnostic, not the product's 30-minute local-day integration.

The [NDBC station 46088 reference](https://github.com/MarineCast/toolkit-meteorology/blob/main/validation/references/ndbc_46088_2024-01-02.txt)
retains all 144 ten-minute records for 2024-01-02 with the source archive hash
in its [metadata](https://github.com/MarineCast/toolkit-meteorology/blob/main/validation/references/ndbc_46088_2024-01-02.meta.json).
The [24 HRRR scalar samples](https://github.com/MarineCast/toolkit-meteorology/blob/main/validation/pilot_inputs/hrrr_46088_2024-01-02_hourly.json)
and [comparison report](https://github.com/MarineCast/toolkit-meteorology/blob/main/validation/reports/ndbc_46088_2024-01-02.json)
show 24 exact-hour matches: temperature MAE 0.49°C, mean sea-level pressure
MAE 0.43 hPa, wind speed MAE 0.73 m/s, gust MAE 1.17 m/s, and mean absolute
non-calm wind-direction error 16.3°. NOAA lists this buoy's wind sensor at
3.8 m and air-temperature sensor at 3.4 m, whereas HRRR's fields are 10 m and
2 m. No height or averaging-period adjustment was applied. No acceptance
threshold was set for this one-site, one-day weather pilot.

Reproduce the offline comparisons with:

```bash
python -m meteorology.maintenance.validate_astronomy \
  validation/references/usno_2024_solar_lunar.json
python -m meteorology.maintenance.validate_horizons \
  validation/references/jpl_horizons_2024_altitudes.json
python -m meteorology.maintenance.validate_ndbc \
  --observations validation/references/ndbc_46088_2024-01-02.txt \
  --model validation/pilot_inputs/hrrr_46088_2024-01-02_hourly.json \
  --station-metadata validation/references/ndbc_46088_2024-01-02.meta.json \
  --date 2024-01-02
```

## Inputs to freeze before comparison

Archive the exact generated release and its manifest, source inventory,
crosswalks and samples. Record station/buoy provider, version, rights, site
coordinates, sensor height, quality flags, measurement time basis and unit.
Preserve rejected and missing observations with reasons. Choose sites, seasons,
years and acceptance thresholds before inspecting the errors. Keep a disjoint
holdout by site and time; do not tune model methods on that holdout.

## Prespecified comparisons

| Product | Independent comparator | Alignment and metrics |
| --- | --- | --- |
| 10 m wind | Quality-controlled coastal stations and buoys with recorded anemometer height | Compare U/V and speed after documented height/time alignment; report bias, MAE, RMSE, circular direction error for non-calm cases, and site/season counts. Include edge/coastal cells and a projection-based GRIB rotation oracle. |
| Temperature, humidity, pressure, cloud and visibility | Quality-controlled regional observations with matching units and support | Match valid time and nearby H3/source grid; report site and season stratified bias, MAE, RMSE, coverage, and distance sensitivity. Never treat a point sensor as an H3 polygon mean. |
| Precipitation | Gauge or buoy observations with known accumulation intervals | Compare six-snapshot rate estimate to a matched interval only; report dry/wet classification, bias and error by intensity. Separately test sensitivity to the nominal four-hour extrapolation. Do not call this a measured 24-hour total. |
| Solar and lunar context | Independent ephemeris for declared coordinates, horizon and timezone | Compare sunrise/sunset or daylight hours, solar altitude, lunar altitude/phase and sampled dark-visible hours across solstices, equinoxes, leap day, polar cases and both DST transitions. State the approximation error distribution. |

Each report should contain the independent source citation and retrieval date,
input hashes, producer commit and method IDs, geographic/temporal coverage,
alignment rules, rejected records, stratified metrics and threshold decisions.
`meteorology benchmark` compares acquisition throughput and completeness,
not scientific accuracy. The new NDBC comparator is a one-day diagnostic;
a regional benchmark still needs multiple reviewed sites, seasons and years,
historical site metadata, and a prespecified time/height/quality contract.
Prospective use also needs an
as-issued forecast source and a separate issue-time availability gate; the
current retrospective f00 product is not such a forecast.
