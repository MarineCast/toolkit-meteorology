"""Source-specific request dependencies; never imply a complete legacy family."""

from __future__ import annotations

from dataclasses import dataclass


class UnsupportedVariableError(ValueError):
    """A source has no qualified implementation of the requested quantity."""


@dataclass(frozen=True)
class Variable:
    metrics: tuple[str, ...]
    units: tuple[str, ...]
    hrrr: tuple[str, ...] = ()
    era5: tuple[str, ...] = ()
    reduction: str = "mean"


VARIABLES = {
    "temperature": Variable(("TEMPERATURE_2M_C",), ("degC",), ("temperature_2m_k",), ("t2m",)),
    "wind": Variable(
        ("U_WIND_10M_MS", "V_WIND_10M_MS", "WIND_SPEED_10M_MS"),
        ("m/s",) * 3,
        ("u_wind_10m_ms", "v_wind_10m_ms"),
        ("u10", "v10"),
    ),
    "cloud_cover": Variable(
        ("TOTAL_CLOUD_COVER_PCT",), ("percent",), ("total_cloud_cover_pct",), ("tcc",)
    ),
    "pressure": Variable(
        ("MEAN_SEA_LEVEL_PRESSURE_HPA",), ("hPa",), ("mean_sea_level_pressure_pa",), ("msl",)
    ),
    "relative_humidity": Variable(
        ("RELATIVE_HUMIDITY_2M_PCT",), ("percent",), ("relative_humidity_2m_pct",), ("t2m", "d2m")
    ),
    "dewpoint": Variable(("DEWPOINT_2M_C",), ("degC",), era5=("d2m",)),
    "visibility": Variable(("VISIBILITY_KM",), ("km",), hrrr=("visibility_m",)),
    "precipitation": Variable(("PRECIPITATION_MM",), ("mm",), era5=("tp",), reduction="sum"),
    "snowfall": Variable(
        ("SNOWFALL_WATER_EQUIVALENT_MM",), ("mm water equivalent",), era5=("sf",), reduction="sum"
    ),
}
ALIASES = {"precip": "precipitation"}
ERA5_NAMES = {
    "t2m": "2m_temperature",
    "d2m": "2m_dewpoint_temperature",
    "u10": "10m_u_component_of_wind",
    "v10": "10m_v_component_of_wind",
    "tcc": "total_cloud_cover",
    "msl": "mean_sea_level_pressure",
    "tp": "total_precipitation",
    "sf": "snowfall",
}
SOURCES = {
    "hrrr": dict(
        outputs=["h3", "native"],
        default_output="h3",
        h3_resolutions=[5],
        earliest_date="2020-12-03",
        scope="CONUS v4 Lambert 3 km; decoded footprint required",
        payload="one nationwide GRIB message per selected field and hour",
    ),
    "era5": dict(
        outputs=["native"],
        default_output="native",
        h3_resolutions=[],
        earliest_date="1940-01-01",
        scope="regular 0.25 degree CDS grid, no dateline/polar halo",
        payload="one bounded regional GRIB job per UTC day plus accumulation boundary",
    ),
}


def dependencies(variables, source):
    if source not in SOURCES:
        raise ValueError("data_source must explicitly be hrrr or era5; no source substitution.")
    if isinstance(variables, str):
        variables = [variables]
    try:
        variables = list(variables)
    except TypeError:
        raise ValueError("variables must be a sequence of supported variable names.") from None
    if any(not isinstance(v, str) for v in variables):
        raise ValueError("Every variable must be a supported string name.")
    names = sorted({ALIASES.get(v, v) for v in variables})
    if not names:
        raise ValueError("At least one variable is required.")
    required = set()
    for name in names:
        if name not in VARIABLES or not getattr(VARIABLES[name], source):
            detail = (
                "HRRR interval accumulation and snowfall are unqualified; the six-rate "
                "legacy estimate remains available through build_surface_weather."
                if source == "hrrr"
                else "ERA5 visibility is unavailable; no proxy is substituted."
            )
            raise UnsupportedVariableError(f"{source} has no qualified {name} request. {detail}")
        required.update(getattr(VARIABLES[name], source))
    return names, sorted(required)


def available_variables(source):
    if source not in SOURCES:
        raise ValueError("Unknown source; select hrrr or era5.")
    return {
        name: dict(
            metrics=list(v.metrics),
            units=list(v.units),
            inputs=list(getattr(v, source)),
            daily_reduction=v.reduction,
            supported=bool(getattr(v, source)),
        )
        for name, v in VARIABLES.items()
    }
