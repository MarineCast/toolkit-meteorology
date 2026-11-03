# Radiation and waves investigation — 2026-11-01

**Subsequent authorized implementation, 2026-11-01:** Solar Radiation now has its
own [pipeline and contract](solar-radiation.md). The original investigation below
records its pre-implementation scope; it is not the current radiation-delivery
status. A subsequent request also implemented Waves in `toolkit-oceanography`,
with a separate `Data/Oceanography/waves/` delivery for 2009-01-01–2026-10-26.
Its canonical contract is `Toolkits/toolkit-oceanography/docs/WAVES.md` in the
MarineCast workspace. It retains all 463 R5 keys, 21 cells with native wave values
and 442 always null under conservative point support. This supersedes the
investigation-only Waves status below; it does not establish independent coastal
accuracy, viewability or ecological model qualification.

This is a source/documentation and local-code review. No radiation/wave acquisition,
new provider authentication, scientific qualification or delivery was performed.
The implemented [Solar Cycle](solar-cycle.md) remains deterministic geometry.

## Radiation belongs with Meteorology

Downward shortwave radiation describes solar energy arriving at the surface after
the atmosphere and clouds affect it. Solar geometry helps interpret radiation,
but cannot reconstruct actual all-sky radiation. A separate **Solar Radiation**
product is the recommended next implementation, not an adopted contract.

The [official ECMWF ARCO guide](https://confluence.ecmwf.int/pages/viewpage.action?pageId=505390919)
lists these hourly accumulated fields in the same 0.25° surface store used by weather:

| Candidate | ERA5 short name | Source units | Interpretation |
| --- | --- | --- | --- |
| Surface solar radiation downwards | `ssrd` | J m⁻² | Total downward shortwave energy on the horizontal surface |
| Total sky direct solar radiation at surface | `fdir` | J m⁻² | Direct component; retain source geometry, not an inferred direct-normal quantity |

The [IFS physical-process documentation](https://www.ecmwf.int/sites/default/files/elibrary/112024/81626-ifs-documentation-cy49r1-part-iv-physical-processes.pdf)
explains hourly ERA5 accumulations and conversion to flux. Hourly mean irradiance
is energy divided by 3,600 seconds, in W m⁻². A UTC daily energy total requires all
24 preceding-hour accumulations ending 01:00 through next 00:00, as for rainfall.
Daily mean flux divides that complete energy by 86,400 seconds.

Before implementation, verify actual `ssrd`/`fdir` metadata, coordinates,
accumulation timing, direct-component receiving-plane convention and missing
support in a bounded source sample. Do not relabel `fdir` as direct-normal irradiance
or divide hourly energy by one instantaneous solar cosine. Derived diffuse
radiation needs matched supports and a documented physical convention. Genuine
night zeros remain distinct from missing source records.

`regional_source.py` pins seven native weather fields; `compute.py` and the weather
validator expose seven families. Radiation needs an explicit implementation
extension, not a new variable spelling. Reuse the archive design with separate
lineage, accumulation checks and coastal validation. Preserve accepted weather
vintages/files. Historical operational availability remains unproven.

## Waves belong with Oceanography

The [ARCO guide](https://confluence.ecmwf.int/pages/viewpage.action?pageId=505390919)
provides a **separate wave store on a 0.5° grid**, containing:

| Candidate | ERA5 short name | Units |
| --- | --- | --- |
| Significant height of combined wind waves and swell | `swh` | m |
| Mean wave period | `mwp` | s |
| Mean wave direction | `mwd` | degrees |

This is a feasible global retrospective candidate for `toolkit-oceanography`.
Verify its native geometry, wet mask, gaps and direction convention independently;
the atmospheric crosswalk cannot be reused blindly. Distinguish mean from peak
period and circularly aggregate directions. Define any sea-state scale explicitly:
wind-derived Beaufort, wave-height categories, measured sea state and sighting
detectability are different quantities.

The current local Oceanography catalog has no delivered wave family. Existing
temperature, salinity, currents, tide and biogeochemistry products do not supply
wave height or sea state. A coarse global wave grid does not establish support
inside narrow Salish Sea channels, sheltered inlets or small bays. Keep unsupported
locations null. Coastal delivery needs regional wave-model/buoy coverage review,
water-connected mapping and independent validation before inland claims.
No Oceanography code or data was changed in this investigation.
