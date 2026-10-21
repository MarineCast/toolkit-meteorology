# Product contract

The sole supported output is exact approved coastal R5 ERA5 weather, described in
[the API contract](regional-api.md). Current output uses
`era5-retained-exact-coastal-r5-v1`; direct native production records
`ecmwf-arco-wide-h3-utc-v1` as source_method. Legacy rectangle catalogs remain
readable for the original retained publication. This does not make rectangle,
HRRR, astronomy or model-feature outputs supported products.

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

A single local settings preflight rejects missing/invalid effective configuration
before study loading, source acquisition or publication. This validates local
settings, not provider access or source availability. A present settings file or
CLI run requires all six values; explicit file-free API calls retain validated
optional defaults.
