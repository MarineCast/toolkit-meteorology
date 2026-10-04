# Compact daily and weekly weather summaries

The `meteorological.weather_summary` export consumes validated hourly f00 releases.
It provides retrospective weather context for daily/weekly modeling; it does not
produce future weather forecasts or establish species-specific predictive skill.
The existing six-snapshot daily product and its precipitation estimate are unchanged.

## Contract

- Input: one complete hourly release per local day, sharing timezone, R5 cell set,
  bounds, availability policy, lag, method and source evidence kind. Duplicate dates,
  missing days inside the requested range and mixed synthetic/real sources fail.
- Output: `weather-summary.parquet` plus a standard `MANIFEST.json`. One row is
  period (local civil day or Monday-start calendar week) × spatial scope × metric.
  Native-H3 rows retain `H3_INDEX`; whole-region rows use a null H3 index and
  `SPATIAL_SCOPE=region`. The region is the exact configured atmospheric support,
  including land and water. A null H3 index never means missing spatial identity.
- Statistics: sample mean, minimum and maximum for the nine current hourly core
  metrics. Regional statistics weight each H3 centroid sample equally; they are
  not geographic area integrals. Multiple cells may sample the same native point.
  Weekly means use sums/counts of hourly samples, not unweighted daily means.
  U/V means remain vector components; mean speed is a scalar-speed mean.
- Time: typed UTC interval start/end and availability, local start date and timezone.
  Weeks cover full Monday-to-Monday local boundaries, including DST (167/168/169
  hours). Edge weeks retain full-week denominators and are explicitly partial.
- Missingness: `VALID_CELL_HOURS`, `EXPECTED_CELL_HOURS`, `COVERAGE_FRACTION` and
  `STATUS` distinguish COMPLETE, PARTIAL and UNAVAILABLE. With `--as-of-utc`, only
  source rows whose assumed availability is at/before the cutoff contribute.
  Empty statistics are null; observed zero stays zero. `AVAILABLE_AT_UTC` is the
  latest assumed availability of contributing rows, not observed publication.
- Provenance: export revalidates each source deeply, pins its publication family
  while reading and retains checksummed source manifest/artifact references.
  The export does not duplicate raw grids or automatically delete any inputs.
  Source rights and synthetic labels are inherited. No new redistribution permission
  is conferred. Product validation checks source-reference checksums and summary
  structure/ranges/coverage; it does not rerun every source's scientific validation.

## Resource behavior

The exporter processes one daily release at a time, projects only identity/time/core
columns and retains one week's per-cell accumulators. Output uses Zstandard
compression with daily/weekly row groups. Deep source validation still reads each
source's retained grids and holds one day's H3 samples; memory is bounded in the
number of input days, not necessarily small for an arbitrarily large region.
Source manifests/checksums remain proportional to the number of input days.
No runtime, peak-RSS, accuracy or storage reduction is claimed without measurement.

```sh
meteorology export-weather-summary \
  --manifest /path/to/day1/MANIFEST.json \
  --manifest /path/to/day2/MANIFEST.json \
  --output-dir /path/to/new-summary \
  --spatial-scope both
meteorology validate --manifest /path/to/new-summary/MANIFEST.json
```

Use repeated `--manifest` arguments for a contiguous range of frozen daily releases.
`--spatial-scope` accepts `h3`, `region`, or `both` (default). `--as-of-utc` is an
optional explicit UTC cutoff for historical availability filtering. The output
files must not already exist; regeneration uses a new destination. The complete
range is inferred from the source manifests. Freeze sources before replacing daily
working releases if you want independently retained input generations.

For prospective forecasting, train/evaluate with inputs available at each forecast
issue time. An assumed fixed source lag is not measured publication evidence.
The exporter does not add precipitation, astronomy, or unverified new HRRR fields.

## Bounded synthetic resource check (2026-10-04)

An installed-wheel run over the two frozen synthetic DST fixtures used by
`test_compact_summary_dst_asof_lineage_and_relocation` covered 450 cells and 47
UTC hours (March 9–10, 2024). The source hourly Parquets totalled 34,114 bytes.
Both daily/weekly H3 and regional summaries totalled 19,149 Parquet bytes (12,177
long-form rows); regional-only output was 13,161 bytes (27 rows). Each export
included deep input validation and took approximately 3.77 and 2.16 seconds,
respectively, on the local machine. Combined process peak RSS was about 317 MiB,
including imports and both sequential exports.

These highly repetitive synthetic values compress unusually well. Sizes exclude
manifests and retained decoded/source evidence. Adding summaries does not shrink
existing storage, and no input retention policy was changed. This checks the
implementation on a bounded fixture; it is not a real-HRRR throughput, memory
scaling, accuracy or regional representativeness benchmark.
