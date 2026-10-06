# Separate ERA5 and HRRR retrospective products

ERA5 supplies a consistent requested 2009–2026 retrospective baseline. HRRR
supplies a separate higher-resolution operational analysis product over its actual
available eras. There is no source blending, ERA5 replacement of missing HRRR,
or artificial ERA5 detail at H3 resolution. Both use UTC days and actual verified
coverage, not an assertion that the whole requested window has been acquired.

## Contract and current implementation

The shared coastal policy approves ocean-side water within 22224m plus requested
inland waters. Its bbox is a planning/acquisition envelope. Materialized geometry,
coastline mask and grid registry remain pending. All production gates reject that
state, including when `selection_policy` is removed: the coordinated schema now
requires policy, envelope role and geometry status even for planning. Old rectangle
configs need explicit migration. Source interpolation halos are separate from the
reporting universe and do not justify far-offshore reporting.

The installed `era5-plan` command prints exact calendar selections, one next-day
00UTC precipitation request, caps and credential-presence checks without network
access. It defaults to a two-day pilot, and allows up to 31 days only by explicit
chunk planning. It cannot plan the entire history in one request.

```sh
meteorology --study-config /path/to/study.v1.json era5-plan \
  --start 2024-01-01 --end-exclusive 2024-01-03
meteorology --study-config /path/to/study.v1.json hrrr-plan \
  --start 2024-01-01 --end-exclusive 2024-01-03
```

`era5.source.decode_grib` yields one normalized message at a time with optional
ecCodes (`pip install '.[era5]'`). It verifies ECMWF centre, ERA5 class/stream,
parameter ID, units, analysis versus forecast identity, expver, 0.25-degree regular
distribution grid, valid time, precipitation step bounds and wind orientation.
Its raw records retain native point, source-file checksum, parameter, retrieval
instant, exact interval and final/preliminary consolidation status. GRIB is required:
a homogeneous NetCDF download can lose expver discrimination. Bitmap nulls stay
null. The decoder has not yet been qualified against an authenticated CDS payload.

`era5.daily.utc_daily` accepts one day's captured native records plus its next
midnight boundary and returns a wide native-grid dataframe. Each metric has
status, valid-hour count, expected count 24 and coverage. A metric's daily value is
null unless all 24 slots are finite. Temperature, dewpoint, wind components,
hourly wind speed, pressure and cloud use sampled daily means. RH is derived at
each hour as 100*es(Td)/es(T) using the explicitly pinned Bolton liquid-water
approximation `es(Tc)=6.112*exp(17.67*Tc/(Tc+243.5))`; it is not directly archived
2m RH nor a claim to reproduce the IFS mixed-phase implementation. Supersaturated
pairs are rejected, not clipped. Visibility and gust are explicitly unavailable
in this minimal source selection; no proxy is substituted.

CDS ERA5 total precipitation is already an hourly accumulation ending at valid
time. Daily mm is 1000 times the sum of 01–23UTC and next-day 00UTC. No deaccumulation
of already-hourly values occurs. ERA5-Land/cumulative MARS inputs and zero-length
f00 accumulations are rejected. Dry complete input is zero; missing input is null.

Within a captured release expver 1 takes priority over 5 for the same point/hour/
parameter. Same-expver duplicates fail. A missing final value does not silently
fall back to preliminary. Every released vintage must remain immutable; a later
consolidation makes a new release and receipt. Retrieval as-of is explicit and
historical provider publication time is unknown, not fabricated from a 5-day lag.

These APIs are source-processing helpers with synthetic verification. They do not
publish a final Data release, generate a coastal mask, implement a marine H3
crosswalk, submit CDS jobs, or claim real-source completeness. Production publication
and actual authenticated bounded acquisition remain gated integration work.

## Access and source facts

[CDS setup](https://cds.climate.copernicus.eu/how-to-api) requires local personal
token configuration and manual dataset terms acceptance from the
[ERA5 download form](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels?tab=download).
The adapter checks file/environment presence without reading tokens, creating a
client, accepting terms or submitting data jobs. Tokens must stay outside chat.

[ERA5 documentation](https://confluence.ecmwf.int/spaces/CKB/pages/76414402/ERA5+data+documentation)
identifies final expver 1, preliminary expver 5 and possible ERA5T revisions before
consolidation. The CDS regular 0.25-degree distribution is regridded from coarser
model-native information. Repeating a value at multiple H3 cells adds no resolution.
[ECMWF accumulation guidance](https://confluence.ecmwf.int/spaces/CKB/pages/197702790/Conversion+table+for+accumulated+variables+total+precipitation+fluxes)
defines the midnight convention above. ERA5-Land is a different product and is not
this marine baseline.

[NOAA HRRR history](https://rapidrefresh.noaa.gov/hrrr/) lists implementation dates
2014-09-30 (v1), 2016-08-23 (v2), 2018-07-12 (v3), 2020-12-02 (v4). Those calendar
cutovers are not a guarantee of every cycle or field. Transition days require cycle
metadata. [NOAA's AWS archive](https://registry.opendata.aws/noaa-hrrr-pds/) is public;
its archive-since-2014 description does not certify complete 2014 coverage. Pre-
operational HRRR remains unavailable. Native footprint must be requalified by era;
unsupported northern coastal water stays missing. Existing HRRR builders remain
separate and their six-snapshot precipitation estimate is never relabeled a true
hourly UTC daily accumulation.

## Resource and retention plan

Start after access and source-budget approval with one two-day ERA5 pilot. Suggested
caps: 128MiB transfer, 256MiB staging, 512MiB RAM, 1 worker, 1h local compute. Transfer
size is unknown until measured; the planner's float64 lower bound is not an HTTP
quote or Python peak memory estimate. With roughly 21GiB free at preflight, do not
reserve the entire historical hourly H3 matrix. The coast worker's separate 4GiB
staging/1GiB RAM reservation is not available to meteorology.

Decode one bounded source chunk and one day's records at a time; retain a single
canonical regional source/compact native series, exact checksums and small daily
products/crosswalks. Do not retain source GRIB, full decoded hourly grids, H3 hourly
copies and frozen duplicates for every date. No deletion of existing archives is
authorized. A measured pilot decides retention and a 31-day chunk budget before
any historical-scale request.

HRRR first pilots: representative old, transition-adjacent and recent cycles,
separate from ERA5. The plan allows a two-day UTC core pilot capped at 768MiB transfer,
2GiB staging, 1GiB RAM, one worker, 1h compute. Prior ~266.5MiB/day transfer was measured
only on the earlier small domain. It does not establish a budget for the revised
reach. Large historical acquisition is disabled pending native support and measured
transfer/retention review. Use compact hourly point samples/crosswalks/receipts after
verified decode; the old live demo retaining every cropped grid is not the proposed
full-history retention path. An actual streaming HRRR backfill executor is still
pending this qualification.

## Checks

```sh
python -m pytest -q tests/test_era5.py tests/test_shared_study.py
python -m pytest -q
```

The live NOAA test stays opt-in; no provider request is needed for these regressions.
