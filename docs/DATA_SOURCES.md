# Sources, attribution and limits

[Documentation index](README.md)

## NOAA/NCEP HRRR

The implemented weather backend uses NOAA/NCEP HRRR surface GRIB data discovered through Herbie.
Core weather uses forecast-hour-zero analyses; precipitation uses matched forecast-hour-one rates.
Required attribution is **NOAA/NCEP HRRR**. Inventory and manifest fields preserve source URIs,
valid times, precipitation issue/lead times, grid identity, checksums and availability information.

HRRR is a United States government data product. Before redistributing source-derived data, verify
the authoritative archive's applicable terms. This repository bundles software and reference
metadata, not provider datasets. Open-Meteo, HRRR Zarr and historical ERA5 backfills are not
configured backends of this package.

## Deterministic astronomy

Daylight and lunar products use local approximate calculations and require no ephemeris download.
They do not account for terrain horizons, refraction, clouds, sea state or artificial lighting.
They are environmental covariates, not direct observations of visibility or species occurrence.

The detailed source contract retained with the installed package is
[`meteorology/DATA_SOURCES.md`](../src/meteorology/DATA_SOURCES.md).
Software licensing is recorded in [LICENSE](../LICENSE); software rights and dataset rights are
separate. Source access and GRIB decoding were not exercised by the extraction's offline validation.
