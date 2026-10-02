# Meteorological environmental family

The meteorological family uses explicit acquisition, offline build, and inspection contracts.
It intentionally uses full model-bounding-box atmospheric H3 support, including land and water;
seascape's wet-cell universe is not a valid substitute.

## Lifecycle

| Product | Acquire | Build | Inspect | H3 |
|---|---|---|---|---:|
| Spatial support | — | `spatial_support/build.py` | `spatial_support/inspect.py` | 4, 5, 6 |
| Surface weather | `surface_weather/download.py` | `surface_weather/build.py` | `surface_weather/inspect.py` | 5 |
| Hourly weather | `hourly_weather/download.py` (retained decoded input) | `hourly_weather/build.py` | `hourly_weather/inspect.py` | 5 |

The distinct hourly method, schema, source support, missingness and offline
producer are documented in [hourly weather](hourly-weather.md).
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

The acquisition retains one region-sized sample per timestamp plus the source-grid
crosswalk. New files use immutable serialized-SHA-256 paths under `raw/objects/samples/`
and `raw/objects/crosswalks/`; the inventory stores the relative path and checksum of each.
Older timestamp paths remain readable and are never overwritten by a refresh. The
`resolved_config.sample_storage` value `immutable-sha256-objects-v1` identifies this storage
layout without changing the meteorological method or Arrow schema. Successful candidate objects,
`raw/runs/<token>/WORKING_INVENTORY.parquet`, and the ordinary working inventory survive failures
for resumption; the
canonical acquisition inventory and manifest are published only when the frozen requested range
is complete. Publication checks the exact six-instant schedule for every local date from the
declared start through end, with no duplicate or missing instants. It rechecks every retained
sample, crosswalk, provenance field and configured availability lag, including rows from earlier
requests. A disjoint request, stale earlier lag, or unrelated failed working row leaves canonical
metadata unchanged; request the missing or stale dates explicitly to complete the range.

Each live HRRR decode checks the configured box against the decoded native grid-edge
polygon and checks that the crop contains nearby source points at every box corner.
The crosswalk checks every H3 centroid against that polygon and uses a nearest-point
distance allowance calculated from the decoded grid's 99th-percentile adjacent
spacing times √2. Reused crosswalks receive the same checks when source data are
fetched. The manifest records `decoded-native-footprint-and-spacing-v1`; legacy
rows must be reacquired with `--overwrite` for the complete frozen range before
they can be published under this policy. An in-progress acquisition retains a
policy marker so it can resume without redownloading its validated rows.

Publication additionally records `row-bound-current-source-v1` acquisition evidence.
A network-free snapshot requires the exact current source manifest and its
checksummed canonical inventory; the workspace policy marker is insufficient.
Newly acquired rows retain evidence bound to their sample/crosswalk and support
checksums, source identity, and method/field/spatial policy. These records permit
interrupted live acquisition to resume without promoting unevidenced older rows.
A verified current canonical manifest from before this extra evidence record
remains eligible under its documented spatial contract. Historical releases
retain their original claims.

One writer owns the raw workspace from seed read through publication. A second writer or a
reader seeking a canonical metadata snapshot receives an explicit busy error. Readers also
refuse an unfinished metadata journal until recovery. The existing family publisher promotes
the inventory and then the terminal manifest under a durable journal; its `committed` journal
checkpoint is the authoritative commit. Recovery rolls back an interrupted `promoting` phase
and retains a `committed` generation. The next acquisition or snapshot run performs recovery
before reading its seed. Immutable sample and crosswalk references remain valid on either side
of that decision. A killed process can leave `ACQUIRING` run state and unreferenced objects;
these are retained for inspection. There is no automatic garbage collection or retention
policy in this patch.

The inventory separates the logical `noaa-hrrr://` object identity from the actual `SOURCE_URI`
and `PRECIP_SOURCE_URI` retrieval URLs and their retrieval times. HTTPS/S3 retrieval URLs
must carry the expected object key; a matching path alone does not establish mirror ownership
or byte equality with another endpoint. Sample and crosswalk checksums cover the
retained derived files; raw GRIB bytes are not archived or independently checksummed by this
toolkit. A network-free snapshot requires an existing current acquisition inventory with this
provenance; sample Parquet files alone cannot reconstruct retrieval history.

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

The build pins a checksum-backed metadata snapshot before reading samples. A later acquisition
refresh can publish a new canonical inventory without changing the build's input generation.
Weather manifests cite the consumed snapshot; validation and freezing copy the referenced
inventory and immutable sample objects. Matrix export pins each source family while reading, and
freezing pins its source family while copying. Metadata snapshots are retained under the raw
workspace for reuse and need an explicit retention policy before cleanup.

Every published date contains the exact configured support, all six four-hourly `f00` core-weather
analyses, and six matched `f01` precipitation rates initialized one hour earlier and valid at the
same timestamps. Missing, ambiguous, non-finite, or unsupported inputs
block publication and are never converted to zero. A fully dry range is valid when all six
source samples per date pass those checks. `PRECIP_MM_DAY_ESTIMATE` is explicitly the sum
of the six `f01` rates multiplied by four hours; it is not a true hourly or accumulated 24-hour
precipitation analysis.

The decoded GRIB wind-reference flag is required for both 10 m components. Grid-relative
components are rotated using an orthonormal local axis basis derived from the decoded grid axes
before R5 sampling; published U/V and
FROM direction are earth-relative. Missing or conflicting flags block acquisition. The sample
schema records `WIND_VECTOR_BASIS=earth_relative`, so older cached samples cannot be reused
as if they had been rotated. Changing the support bounding box or inclusion method requires
rebuilding support before acquisition or product builds.
`AVAILABLE_AT_UTC` is a versioned assumed fixed-lag policy, not a measured provider
publication timestamp. A changed lag invalidates cached samples; acquisition and build
check every retained sample timestamp against the current policy and inventory. The current
acquisition, weather and daylight method IDs are v4, v5 and v3 respectively; old releases retain
their original method IDs and require new generation to gain these contracts.

Before recoverably archiving legacy weather artifacts, run the complete-range and shared-core
parity gate:

```bash
python -m meteorology.surface_weather.verify \
  --config config/data/environment_meteorological.yaml
```

The migration command refuses to run unless that report passes and is bound to the current weather
manifest checksum.
Arithmetic matches to a subset of historical samples are recorded as suspected legacy
missingness, not evidence sufficient to exclude a date. Unexplained cloud differences remain
comparison failures; a selector explanation requires independent historical source evidence.
Execution holds an exclusive migration lock. On interrupted moves, recovery compares the journal
checksum with retained source/archive copies. Differing copies remain in place with a `CONFLICT`
journal for manual resolution; successful rollback retains a `MIGRATION_RECOVERY.json` record.
The configured custom archive root is included in recovery discovery.

Publication is staged and manifest-validated before promotion. Inspectors verify manifest
checksums before rendering to `outputs/domains/environmental_layer/meteorological/<product>/`.
Interrupted multi-artifact promotions are recovered from a durable transaction journal on the
next run. Product manifests verify both upstream inputs and outputs and identify dirty-worktree
builds with a scoped source hash.

The meteorological feature catalog assigns every field both `role` and `variable_kind`.
Coverage, availability, lineage, sampling distance, calendar bookkeeping, and QC are metadata.
The retired atmospheric-viewability, event-hour, storm, and lightning products are not part of the
canonical meteorological family.

The current field acceptance policy is `meteorology-field-contract-v1`. Arrow schemas define
ordinary nullability; shared hard limits apply during acquisition, weather build, deep validation
and matrix validation. Mean sea-level pressure has a hard 700–1200 hPa range. Values outside
800–1100 hPa are regional diagnostics to investigate, not automatic failures. The latter range
has no independent physical justification as a universal reject threshold. Conditional calm and
astronomy null rules remain explicit paired checks. Manifests and matrices from before this
policy remain checksum-readable, but require the validator pinned to their earlier revision for
deep scientific certification. Deep validation reports counts under
`regional_warning_counts`; a warning count is not a validation failure.

## Versioned output contract

Current family manifests use schema version 3 and record `software_version`,
`method_version`, `run_id`, `release_id`, resolved configuration and hashes for
declared inputs and outputs. Schema version 2 manifests remain readable for
historical checksum inspection. Deep validation targets current method/schema
versions. The [method registry](methodology.md#provenance-and-reproducibility)
identifies changes in numerical meaning separately from the software version.
Known earlier schema-3 methods remain readable with checksum verification. Deep scientific
validation requires the matching archived validator; the current validator only certifies
current method IDs. See the [archived validator handoff](archived-validation.md) for pinned
revisions and isolated invocation.

Daily wind direction is meteorological **FROM** direction calculated from the
mean 10 m U/V vector; its Arrow field is nullable for calm mean vectors.
`SOLAR_ELEVATION_DAYLIGHT_MEAN_DEG` is null when sampled daylight is absent.
Lunar night fractions and their matching weight are null if `NIGHT_HOURS=0`.
These nulls are not zeros. The [variable inventory](reference/variables.md)
contains field-level units, source identity, processing and ranges.
Integrated solar/lunar hour fields are checked against each row's local civil-day duration,
which can be 23, 24 or 25 hours; geometric `DAYLIGHT_HOURS` retains its 0–24-hour range.
The compact daylight lookup carries `MONTH_DAY`, `IS_LEAP_DAY` and `SOLAR_DAY_365` beside
`DAY_OF_YEAR`; its weight preserves the selected daily value without maximum scaling.

The default ecological model matrix is governed separately from the complete scientific products:

```bash
python -m meteorology.modeling.feature_policy
python -m meteorology.modeling.feature_policy --check
```

That policy excludes metadata and exact deterministic aliases. It does not delete producer columns.
`apply_feature_policy` accepts the exported matrix's native component prefixes and preserves
`DATE`, `H3_INDEX` and `H3_RESOLUTION`; R4 and R5 rows remain separate and require an explicit
downstream alignment decision.
