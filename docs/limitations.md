# Scientific and operational limitations

These limits are part of the product contract, not exceptional failures.

- **Model, not observation:** HRRR values are numerical model fields. The approximate 3 km model grid and nearest-centroid mapping cannot resolve every coastal, terrain or street-scale condition. Source-grid distance is exposed so unusually distant assignments can be reviewed. No area-weighted H3 average is provided.
- **Retrospective support:** The toolkit assembles historical f00 analyses and matched short-lead f01 rates. It does not certify future forecast skill. Dates, valid times, initialization times and declared availability are separate because using a future-valid field for a prediction at time `t` would cause leakage.
- **Sparse daily summaries:** Six wall-clock samples discard sub-daily structure. Extrema are extrema of those six values, not a continuous-day minimum or maximum. DST days contain 23 or 25 civil hours while the weather reduction still uses six nominal four-hour points.
- **Precipitation:** `PRECIP_MM_DAY_ESTIMATE` extrapolates six forecast PRATE snapshots. It is not a measured or model-integrated 24-hour accumulation. Do not compare it as if it were one without calibration.
- **Missingness:** A complete daily weather product requires every configured interval for every R5 cell; incomplete days fail rather than becoming zero. Calm mean wind has no direction. Polar-night mean daylight solar elevation and no-night lunar fractions are undefined and null. Metadata distinguishes those nulls from observed zero.
- **Astronomy precision:** Daylight and lunar geometry are approximate. No terrain horizon, refraction, cloud transmission, artificial lighting or ephemeris-grade validation is included. Astronomical illumination is not actual nighttime surface light.
- **Domain:** The weather backend targets HRRR CONUS. A single configured IANA timezone applies to all cells. Global domains, per-cell timezones, antimeridian crossing and climate projections are not supported.
- **Validation evidence:** Offline synthetic tests and a wheel smoke test establish package behavior for those fixtures. Live NOAA access, historical regional rebuild parity, other operating systems and downstream application integration require separate checks. See [production readiness](https://github.com/MarineCast/toolkit-meteorology/blob/main/PRODUCTION_READINESS.md) in the repository for current evidence.

Weather conditions alone do not quantify observer activity, physical viewability, reporting capture, detection probability, species presence or ecological preference. Those quantities need separate data and assumptions.
