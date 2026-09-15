# Meteorological environmental family

The meteorological family uses explicit acquisition, offline build, and inspection contracts.
It intentionally uses full model-bounding-box atmospheric H3 support, including land and water;
seascape's wet-cell universe is not a valid substitute.

## Lifecycle

| Product | Acquire | Build | Inspect | H3 |
|---|---|---|---|---:|
| Spatial support | — | `spatial_support/build.py` | `spatial_support/inspect.py` | 4, 5, 6 |
| Surface weather | `surface_weather/download.py` | `surface_weather/build.py` | `surface_weather/inspect.py` | 5 |
| Daylight | — | `daylight/build.py` | `daylight/inspect.py` | 4 |
| Lunar | — | `lunar/build.py` | `lunar/inspect.py` | 5 |

Run network acquisition separately:

```bash
meteorology download surface-weather \
  --config config/data/environment_meteorological.yaml
```

The only canonical backend is direct NOAA HRRR GRIB acquisition through Herbie. Each valid-time
request retrieves the nine required `sfc/f00` fields in one combined request, validates their
identity and units, then samples immediately to the R5 atmospheric support:

```bash
meteorology download surface-weather \
  --config config/data/environment_meteorological.yaml \
  --workers 4
```

The acquisition retains one checksum-addressed region-sized sample per timestamp plus the source-grid
crosswalk. Successful samples and a working inventory survive failures for resumption; the
canonical acquisition inventory and manifest are published only when the frozen requested range
is complete.

Before a full backfill, compare the one-worker cropped-grid baseline with the four-worker direct-R5
candidate on old, middle, and recent dates:

```bash
meteorology benchmark \
  --config config/data/environment_meteorological.yaml
```

The JSON report records valid-time requests, elapsed time, failures, retained rows, and retained
bytes for both paths and fails unless the direct-R5 candidate is faster with zero failures.

The meteorological build is offline and reads only that canonical inventory and the compact R5
samples:

```bash
meteorology build surface-weather \
  --config config/data/environment_meteorological.yaml
```

Every published date contains the exact configured support, all six four-hourly `f00` core-weather
analyses, and six matched `f01` precipitation rates initialized one hour earlier and valid at the
same timestamps. Missing, ambiguous, non-finite, unsupported, or complete-range all-zero inputs
block publication and are never converted to zero. `PRECIP_MM_DAY_ESTIMATE` is explicitly the sum
of the six `f01` rates multiplied by four hours; it is not a true hourly or accumulated 24-hour
precipitation analysis.

Before recoverably archiving legacy weather artifacts, run the complete-range and shared-core
parity gate:

```bash
python -m meteorology.surface_weather.verify \
  --config config/data/environment_meteorological.yaml
```

The migration command refuses to run unless that report passes and is bound to the current weather
manifest checksum.

Publication is staged and manifest-validated before promotion. Inspectors verify manifest
checksums before rendering to `outputs/domains/environmental_layer/meteorological/<product>/`.
Interrupted multi-artifact promotions are recovered from a durable transaction journal on the
next run. Product manifests verify both upstream inputs and outputs and identify dirty-worktree
builds with a scoped source hash.

The meteorological feature catalog assigns every field both `role` and `variable_kind`.
Coverage, availability, lineage, sampling distance, calendar bookkeeping, and QC are metadata.
The retired atmospheric-viewability, event-hour, storm, and lightning products are not part of the
canonical meteorological family.

The default ecological model matrix is governed separately from the complete scientific products:

```bash
python -m meteorology.modeling.feature_policy
python -m meteorology.modeling.feature_policy --check
```

That policy excludes metadata and exact deterministic aliases. It does not delete producer columns.
