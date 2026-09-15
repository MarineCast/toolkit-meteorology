# Meteorological sources and limitations

## Surface weather

Surface weather uses NOAA/NCEP High-Resolution Rapid Refresh (HRRR) surface analyses at forecast
hour zero. Herbie locates source GRIB messages during the acquisition stage only. The toolkit validates
the requested variable identity and units, computes a source-grid hash, and immediately samples the
source grid to the configured R5 atmospheric support. It retains one checksum-addressed compact
sample per valid time rather than cropped source grids or a duplicate canonical subdaily product.

Retained variables are 2 m temperature and relative humidity; 10 m U/V wind; surface gust and
visibility; total cloud cover; precipitation rate; and mean sea-level pressure. Lightning is not
acquired. The inventory records source/valid/availability times, source URI, sample and crosswalk
paths and checksums, source-grid hash, completeness, and failure reason. A working inventory is
resumable; the canonical acquisition inventory is published only when the frozen requested range
is complete.

The canonical acquisition backend is direct NOAA HRRR GRIB through Herbie. Core weather retains
the selected six-analysis `sfc/f00` provenance contract. Because HRRR f00 PRATE is structurally
all zero, precipitation uses six `sfc/f01` PRATE forecasts initialized one hour earlier and valid
at those same sample times. Open-Meteo and HRRR Zarr are intentionally not configured.

HRRR is a United States government data product. Required attribution is NOAA/NCEP HRRR. Users
redistributing source-derived artifacts should verify the terms of the authoritative NOAA archive
used by Herbie. HRRR
forecast-hour-zero data are retrospective analyses, not future forecasts.

## Deterministic astronomy

Daylight and lunar products use dependency-light approximate solar and lunar calculations. They
require no ephemeris download. The calculations are suitable for daily observer-effort covariates,
but are not ephemeris-grade and do not represent terrain horizons, refraction, clouds, sea state,
or artificial light.

The retired storm severity and atmospheric-viewability products are not part of the canonical
meteorological family.
