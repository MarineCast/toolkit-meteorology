# Independent scientific validation plan

Code, synthetic fixtures and an optional one-cycle provider smoke establish
software behavior and compatibility for their tested conditions. They do not
establish regional accuracy or prospective ecological value. No independent
station/buoy or ephemeris result is claimed by this repository revision.

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
The currently available `meteorology benchmark` compares acquisition throughput
and completeness, not scientific accuracy. A station/buoy benchmark needs a
reviewed reference dataset and its time/height/quality contract before an
accuracy job can produce a meaningful result. Prospective use also needs an
as-issued forecast source and a separate issue-time availability gate; the
current retrospective f00 product is not such a forecast.
