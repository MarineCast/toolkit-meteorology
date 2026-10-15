# Benchmark inventory

Benchmarks are explicit developer tools, not production backends. Run from the
checkout with the installed toolkit; use each script's `--help` for exact options.
Generated GRIB requires the `era5` or `native-pilot` extra. Network modes are
manual and need their documented credentials, source rights and resource caps.

| Script | Purpose | Default access |
| --- | --- | --- |
| `native_processing.py` | Native request read/decode counts | Generated GRIB; offline |
| `request_review.py` | Request reuse and processing operation counts | Generated fixtures; offline |
| `request_lifecycle.py` | Cache lifecycle retention and repeat execution | Mocked transport; offline |
| `era5_backfill.py` | Daily versus monthly CDS execution | Generated GRIB; `--live` acquires CDS data |
| `era5_aggregation.py` | Exact reducer comparison against a retained prior implementation | Local source replay; requires explicit input paths |
| `era5_arco.py` | Exploratory Google ARCO chunk layout and transfer | Public remote metadata; `--run` transfers selected hourly chunks |
| `era5_backend_comparison.py` | Matched CDS/Google seven-field source comparison | Inspect `--help`; explicit phases perform live acquisition |

The default production historical source is **ECMWF geographic Zarr**, implemented
in `meteorology.regional_source`, not either Google benchmark. Its full-area
source/export evidence is documented in [the regional guide](../docs/regional-api.md).
Historical benchmark JSON is retained in `docs/evidence/` to support measured
claims; it is not a current release or an automatically refreshed result.

See [development checks](../docs/DEVELOPMENT.md) and
[historical performance evidence](../docs/era5-backfill-performance.md).
