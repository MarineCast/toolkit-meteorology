# Architecture

The supported path is:

```text
Approved registry/mask + UTC dates + allowed families
  → regional_source.Archive (bounded source/cache)
  → compute (nearest native samples and UTC reductions)
  → regional (wide tables and recoverable publication)
  → check_weather (read-only contract checks)
```

The three production modules are regional.py, regional_source.py and compute.py.
Credentials, study identity and registry validation are small separate helpers.
The core/artifacts package retains transactional publication and recovery; its
name-bound transaction checksums intentionally differ from raw public file SHA256.
`coastal.py` is retained to reproduce the original multi-epoch publication from
existing products. It performs no acquisition and is not the primary path.

The package imports no sibling or application code. No HRRR, GRIB/CDS planner,
modeling, astronomy or visualization module is needed. The runtime dependencies
are h3, numpy, pandas, pyarrow, numcodecs and PyYAML. Source data and generated
products are outside the installable distribution. Metadata snapshots and caches
are preserved; source-vintage orchestration remains explicit.
