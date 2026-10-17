# Legacy removal

This cleanup intentionally narrows the package to the approved coastal weather
product. The last complete legacy implementation is commit
`40689f03030d0ac7451226faaba11b74665a9057` (PR #15); Git history preserves it.

| Removed interface | Supported replacement |
| --- | --- |
| get_weather_request and per-family getters | get_weather(start_date, end_date, variable_list) |
| public get_data/get_metric rectangle exports | get_weather with the approved study registry |
| meteorology get temperature / get weather, backfill, workspace init | meteorology get START END --variables ... |
| legacy manifest validation | check_weather(catalog_path, study_config=...) |
| HRRR, CDS/GRIB pilots/jobs/backfill, daylight/lunar, summaries, model feature policies, visualizers | retired; no equivalent promised by this product |
| native-pilot, era5, acquisition, visualization extras | removed; coastal acquisition uses the core install |

Do not translate legacy source-selective arguments into the new positional API.
Specify the approved study once through MARINECAST_STUDY_CONFIG or study_config.
Use a separate output directory for experiments; calls replace their date range.
The internal rectangle sampling helper is retained only to preserve source mapping
and original retained-product tests. The offline coastal merger remains available
for the two original source epochs. Current Data, caches and owner registry inputs
are unchanged by source cleanup. External callers of removed APIs must migrate;
application integration is outside this change.
