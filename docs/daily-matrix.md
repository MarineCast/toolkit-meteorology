# Daily meteorology matrix

`meteorology export-daily-matrix --manifest WEATHER_MANIFEST --manifest DAYLIGHT_MANIFEST
--manifest LUNAR_MANIFEST --output NEW.parquet` exports one native daily Parquet.
Set the same explicit `--workspace` used for the builds so portable manifest paths resolve.

All three complete native manifests, their input/output checksums, exact date ranges and
timezones are verified. The date ranges must match. Output identity is local `DATE ×
H3_INDEX × H3_RESOLUTION`: native daylight R4 and weather/lunar R5. Weather and lunar join
at identical R5 cells/dates; daylight remains separate R4 rows. Variables retain component
prefixes, native types and values. Other-resolution variables remain null. No spatial
interpolation, resolution transfer, zero filling, temporal averaging or truncation is applied.
The R6 support product has no native daily variables and is not added as empty rows.
Day-of-year daylight lookup remains a separate native artifact.

The policy API accepts this exported frame directly:

```python
import pandas as pd
from meteorology.modeling.feature_policy import apply_feature_policy, load_feature_policy

native = pd.read_parquet("NEW.parquet")
selected = apply_feature_policy(native, load_feature_policy())
```

`selected` keeps `DATE`, `H3_INDEX` and `H3_RESOLUTION`. It retains the separate native R4/R5
rows; consumers must choose a spatial alignment and missingness policy before modeling.

The exporter rejects duplicate/invalid cells, inconsistent resolution, incomplete daily
support, mismatched date ranges/timezones and existing destinations. It streams yearly
row groups to a unique temporary file, validates the complete matrix, then promotes it
with `NEW.parquet.manifest.json` as one recoverable artifact family. Embedded
`meteorology_daily_matrix` metadata retains native manifests, dates, timezone, source
identity, source rights, units, formulas, software/method versions, a source-derived release
ID and limitations. The companion manifest binds that release ID to the Parquet byte checksum.
`meteorology validate --daily-matrix NEW.parquet` checks the companion checksum, exact
scientific schema, required values and ranges, per-date H3 membership, conditional wind/lunar
nulls, and native-resolution nulls.
The file is local research context;
its production does not establish forecasting/model integration or authorize redistribution.

HRRR core weather comprises six four-hourly f00 analyses per local date. Precipitation uses
matched f01 forecast rates initialized an hour earlier: `PRECIP_MM_DAY_ESTIMATE` is a
six-snapshot daily estimate, not a measured or fully accumulated 24-hour rainfall total.
Astronomy is deterministic and approximate. Weather, viewability, detection and occurrence
remain separate quantities. Dates are preserved and are never relabeled as current weather.

Checks: `PYTHONPATH=src python -m pytest tests/test_daily_matrix.py -q` followed by
`PYTHONPATH=src python -m pytest -q`. Validate a regular wheel outside the checkout.
