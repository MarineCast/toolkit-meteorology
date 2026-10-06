# Bounded independent HRRR native UTC-day pilot

This follow-on implements the exact 2024-01-01 UTC eight-core sfc/f00 pilot,
separately from ERA5 and the legacy HRRR products. NOAA/NCEP publicly supplies
HRRR through the NOAA AWS archive; source rights/attribution remain NOAA/NCEP.
No account or licence action is performed. Exact bbox is [-125.5,49,-125,49.5],
with a 0.2-degree native crop halo. The halo supplies source stencils; it does not
create a marine mask or prove a 12-nautical-mile coastal-water selection.

Input grain: one index plus eight exact selected nationwide GRIB2 messages per
UTC analysis hour. Requested valid/initial hours are 00 through 23 UTC, lead zero.
Temperature at 2 m, direct relative humidity at 2 m, U/V at 10 m, surface gust,
surface visibility, total cloud cover and mean-sea-level pressure remain separate
native fields. GRIB headers independently verify NCEP centre, parameter/category,
fixed surface, forecast step, units and the decoded Lambert grid. Neither f00
PRATE nor forecast accumulation is in this source plan. Precipitation and direct
dewpoint remain UNAVAILABLE, null and zero coverage; no source blending occurs.

The full native grid is decoded one message at a time; only the geographic crop
is kept in Parquet. Coordinate/spacing/footprint evidence and grid-relative wind
rotation remain source-bound. The retained native indices are original full-grid
indices. Source bytes, ranges, S3 ETags, actual retrieval instants, unit metadata,
GRIB checksums, index checksums, hourly Parquet checksums and software pins form
the acquisition receipt. Actual historic publication time is unknown; no assumed
availability lag is manufactured. Missing bitmap values remain null.

Resource proposal already accepted by parent: 512 MiB aggregate downloaded bytes,
1 GiB aggregate owned staging, 1 GiB cooperative RSS, 300 HTTP requests, one worker,
60 minutes total active elapsed including acquisition/QA, with a 15-minute initial
processing target. Each index reserves 64 KiB; each selected field reserves its
exact index-derived range before network open. Nominal 216 HTTP requests cover
24*(1+8). Index 404s and failed transfers retain reservations; retries on explicit
resume consume the remaining original budget. No redirects, ambient proxies,
hidden retries or arbitrary mirrors. Range responses must be exact 206 responses,
with matching Content-Range/Length and a qualified S3 ETag. Completed inputs are
checksum-validated before reuse. Failed attempts/partials remain immutable.

A whole-run writer lease binds the plan and persistent counters. Nothing is
silently overwritten or deleted, including private scratch bytes. Cached complete
hours and complete range transfers resume without reacquisition; corrupt caches
fail before any replacement. An absent hour leaves a partial native daily QA
checkpoint with null daily values and honest counts; no terminal day manifest is
written until all 24 source hours and independent QA pass. A later explicit
resume can acquire only missing hours within the original caps. No final H3
release or requested-period completeness is claimed even after a complete pilot.

Independent QA rechecks source/hour/day/cardinality, per-metric finite counts,
mean versus mean hourly wind magnitude, units, pressure conversion, missingness,
unsupported precipitation and the daily status/coverage/null contract. Native
footprint/crop support is verified from decoded coordinates before hour promotion.
Scientific and resource limits stop at cooperative checkpoints; they are not OS
preemption of ecCodes, NumPy, pyproj or filesystem calls.

Header references (producer primary sources):
https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-5.shtml
https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-3.shtml
https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-2.shtml

Before live payload: independent review must clear this follow-on executable
and its exact plan. Tests use mocked requests and generated GRIB; provider data
acquisition and measured CONUS decoder resource behaviour remain unqualified.
The reviewed ERA5 pin e43ea0a is preserved and executed separately.

Install scientific dependencies with `python -m pip install '.[test,native-pilot]'`.
Existing `.[era5]` or `.[acquisition]` installations also supply the codec. The plan
includes SHA256 pins for the producer and its local scientific helper files plus
scientific dependency versions; cache reuse requires those exact pins too.

Offline exact plan:
```
python -m meteorology.hourly_weather.native_day plan --study-config /path/to/study.v1.json
```
After review clears the exact plan, execution is:
```
python -m meteorology.hourly_weather.native_day run --study-config /path/to/study.v1.json --plan-json HRRR_RUNNABLE_NATIVE_DAY_PLAN.json --output /fresh/owned/scratch
```
Use the same owned output directory for explicit resume. A successful terminal
manifest is idempotent; changed cached inputs fail rather than being replaced.

The pressure field is specifically NOAA MSLMA (MAPS system reduction), GRIB2
category 3 parameter 198. MSLET (Eta reduction, parameter 192) has the same units
but is a different field and is rejected. The generated-GRIB regression explicitly
checks the primary-table numeric identity and rejects the neighboring parameter.
