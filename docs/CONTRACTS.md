# Product contract

The weather output is exact approved coastal R5 ERA5 weather, described in
[the API contract](regional-api.md). Current output uses
`era5-retained-exact-coastal-r5-v1`; direct native production records
`ecmwf-arco-wide-h3-utc-v1` as source_method. Legacy rectangle catalogs remain
readable for the original retained publication. This does not make rectangle,
HRRR, former astronomy interfaces or model-feature outputs supported products.
The separately authorized [Solar Cycle](solar-cycle.md) supplies calculated solar
geometry in its own child directory and catalog, without altering weather files.
The separately authorized [Solar Radiation](solar-radiation.md) delivers SSRD/FDIR
energy and irradiance with its own catalog, source epochs and exact interval
conventions. Weather's seven families and legacy vintages remain unchanged.

Preserve source units, UTC interval meaning, hourly/daily keys, exact approved
membership, missingness and unknown revision/operational availability. Do not
relabel source grain as H3-scale information or retrospective data as a forecast.
New values must retain source evidence; old source snapshots must not be silently
substituted. Publication stages files and recovers failures before replacing the
three-file output. Final data and external study/registry inputs are preserved.
Source downloads and arrays belong to a transient owned work area outside delivery.
After full final readback, successful runs remove those payloads, preserving source
receipts/hashes inline in the catalog. Failed runs retain completed objects for resume.
Existing caller-owned/shared files are not swept. Rebuilding after cleanup may require
new downloads and cannot promise an identical historical provider vintage.

Run settings select requested metric families and rebuild mode. Append (the
default) validates existing files, requires approved
membership, preserves existing columns/values/nulls/vintages, and acquires missing
UTC dates plus new metrics across all retained dates. New metric values align by
exact cell/time keys; lineage is scoped per metric when epochs overlap. It fills gaps between disjoint old/new ranges to keep continuous keys.
Rebuild explicitly replaces the selected product range/metrics. Both modes build
an absent product. An append request whose dates and metrics are already present leaves files unchanged.
Combined products record source epochs; publication remains recoverable.
Weather checks require hash-bound inline source evidence and complete, nonoverlapping
epoch coverage for each delivered metric. Legacy unscoped epochs cover all delivered
metrics; endpoint repairs carry separate checked evidence. Missing or inconsistent
lineage fails before no-op or append acquisition. See the
[lineage validation contract](regional-api.md#source-lineage-validation).

Weather can be initialized after either named solar companion. Refresh and normal
Weather runs preserve recognized complete companion directories and reject unrelated
entries; a partial Weather file set still requires explicit rebuild.

A single local settings preflight rejects missing/invalid effective configuration
before study loading, source acquisition or publication. This validates local
settings, not provider access or source availability. A present settings file or
CLI run requires Weather dates; optional settings retain validated defaults.
Bounds select approved coastal/water R5 centres inside the study envelope.
Changing a saved region requires explicit rebuild.

Explicit `meteorology.refresh.refresh_weather` maintenance can fill a documented
missing terminal precipitation interval and append new dates. It preserves all
retained finite values, sums the retained 23 hourly intervals plus the newly
supported boundary, records `endpoint_repairs` alongside retained source epochs,
and compares unchanged old rows exactly before publication. `cache_dir` is a work
base: refresh uses an owned `refresh-<destination-hash>` child and removes its
payloads after final readback. Pre-existing caller sources remain untouched. See the [frontier repair contract](regional-api.md#explicit-frontier-repair-and-refresh).
