# Bounded independent validation pilots

The frozen files in this directory support two preliminary, reproducible
comparisons. They are not a regional accuracy certification. Their source URLs,
retrieval time, and archive checksum or per-case service response are retained
with the inputs. Reports contain case-level errors and explicit limitations.

## Astronomy

`references/usno_2024_solar_lunar.json` contains ten responses from the
[U.S. Naval Observatory one-day Sun/Moon API](https://aa.usno.navy.mil/data/api.html):
eight dates at 48.275°N, 123.7°W and two polar cases at 70°N, 150°W. The
comparison uses the toolkit's geometric daylight formula and lunar illumination
approximation. USNO apparent sunrise/set and noon illumination have different
horizon and time conventions; see the report for the exact alignment.

```bash
python -m meteorology.maintenance.validate_astronomy \
  validation/references/usno_2024_solar_lunar.json \
  --output validation/reports/usno_2024_solar_lunar.json
```

`references/jpl_horizons_2024_altitudes.json` freezes 288 hourly airless
solar/lunar altitude values from the
[JPL Horizons observer API](https://ssd-api.jpl.nasa.gov/doc/horizons.html).
Each of its twelve query records retains the exact URL and SHA-256 of the
full API response. Four Pacific Northwest and two polar UTC days are paired
by site and date; the comparator also counts sampled dark-and-moon-visible
hours at the same hourly UTC timestamps.

```bash
python -m meteorology.maintenance.validate_horizons \
  validation/references/jpl_horizons_2024_altitudes.json \
  --output validation/reports/jpl_horizons_2024_altitudes.json
```

## NDBC buoy 46088

`references/ndbc_46088_2024-01-02.txt` is the January 2 slice of the
[NDBC 2024 historical standard meteorological archive](https://www.ndbc.noaa.gov/data/historical/stdmet/46088h2024.txt.gz).
The adjacent metadata file records the full compressed source SHA-256, station
coordinates and sensor heights from the [station page](https://www.ndbc.noaa.gov/station_page.php?station=46088).
The [measurement description](https://www.ndbc.noaa.gov/faq/measdes.shtml)
defines UTC timestamps, units and missing-value sentinels.

The model sample file contains one nearest-grid scalar per UTC hour and the
corresponding HRRR source object URI. Full GRIB bytes were not retained. To
regenerate these samples with the acquisition extra installed, set `TMPDIR`
to a temporary location and run:

```bash
TMPDIR=/private/tmp python validation/acquire_ndbc_pilot.py \
  --date 2024-01-02 \
  --output validation/pilot_inputs/hrrr_46088_2024-01-02_hourly.json
python -m meteorology.maintenance.validate_ndbc \
  --observations validation/references/ndbc_46088_2024-01-02.txt \
  --model validation/pilot_inputs/hrrr_46088_2024-01-02_hourly.json \
  --station-metadata validation/references/ndbc_46088_2024-01-02.meta.json \
  --date 2024-01-02 \
  --output validation/reports/ndbc_46088_2024-01-02.json
```

The acquisition script sums the selected GRIB message ranges before fetching
and rejects a day above the 1 GB indexed-message limit. It downloads one cycle
at a time through Herbie. It does not measure network overhead or archive the
GRIB files. The NOAA source is public, but archive availability may change.
