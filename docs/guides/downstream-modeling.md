# Downstream modeling

The published tables are environmental context. Choosing features for a prediction target is a separate modeling decision.

## Establish the prediction time first

For any prediction issued at time `t`, keep only source fields whose `AVAILABLE_AT_UTC <= t`. A retrospective f00 analysis valid at `t` may not have been available at `t`; the toolkit's declared availability includes a configurable lag and should be compared with the prediction cutoff. The `f01` precipitation rate is initialized one hour before its valid time, but the historical daily estimate uses all six valid times across a local day. That daily estimate is not an ex-ante feature for a prediction made before the day ends.

Use time-based training/test splits that mirror the intended deployment issue time. Do not random-split neighboring dates/cells and then claim prospective skill. Spatial blocking is useful when adjacent H3 cells share the same HRRR source-grid point.

## Choose interpretable representations

- Wind direction is circular. Encode sine/cosine or use U/V components; treat null calm direction separately. Arithmetic degrees and 0°/360° discontinuities can mislead a model.
- Mean speed, maximum speed, gust and mean-vector speed can be correlated but are not exact aliases. Test multicollinearity rather than treating each as independent evidence.
- Temperature, humidity, pressure and visibility are continuous with units. Fit scaling only on training data; preserve the original units in published products.
- Daylight and lunar age/phase are cyclical. A sine/cosine encoding can avoid discontinuity at year or phase wraparound. The optional package feature policy excludes exact aliases and the raw wind-direction angle from its default ecological model matrix; it is not a universal scientific selection rule.
- Lagged daily values must respect source availability and local-date boundaries. The six-snapshot reduction may hide brief storms, fog or precipitation peaks.
- Missing indicators may be useful, but an unavailable source, unsupported cell, incomplete day, calm direction and no-night denominator mean different things. Do not zero-fill them together.

Weather is not occurrence, effort, reporting or detection probability. If an ecological model connects these quantities, document and test that assumption explicitly. The [variable inventory](../reference/variables.md) labels predictor candidates only as fields requiring further policy and leakage review.
