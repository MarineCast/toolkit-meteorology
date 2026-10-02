"""Versioned scientific interpretations for durable meteorological products.

Change a method identifier whenever the meaning or numerical calculation of a
published value changes, even if its Arrow type is unchanged.
"""

from __future__ import annotations

METHOD_VERSIONS: dict[str, str] = {
    "meteorological.spatial_support": "h3_bbox_centroid_support_v2",
    "meteorological.surface_weather.download": "hrrr_f00_f01_earth_wind_sample_v4",
    "meteorological.surface_weather": "hrrr_surface_daily_earth_wind_v5",
    "meteorological.hourly_weather.acquire": "hrrr_f00_hourly_source_r5_v1",
    "meteorological.hourly_weather": "hrrr_f00_hourly_h3_r5_v1",
    "meteorological.daylight": "daylight_astronomy_v3",
    "meteorological.lunar": "lunar_illumination_v2",
    "meteorological.daily_matrix": "native_resolution_daily_matrix_v2",
}

# Retained releases remain checksum-readable; current producers never emit these methods.
HISTORICAL_METHOD_VERSIONS: dict[str, frozenset[str]] = {
    "meteorological.spatial_support": frozenset({"h3_bbox_centroid_support_v1"}),
    "meteorological.surface_weather.download": frozenset({"hrrr_f00_f01_nearest_sample_v1", "hrrr_f00_f01_earth_wind_sample_v2", "hrrr_f00_f01_earth_wind_sample_v3"}),
    "meteorological.surface_weather": frozenset({"hrrr_surface_daily_v2", "hrrr_surface_daily_earth_wind_v3", "hrrr_surface_daily_earth_wind_v4"}),
    "meteorological.daylight": frozenset({"daylight_astronomy_v2"}),
    "meteorological.daily_matrix": frozenset({"native_resolution_daily_matrix_v1"}),
}


def method_version(product: str) -> str:
    """Return the registered method identity for a supported product."""

    try:
        return METHOD_VERSIONS[product]
    except KeyError as exc:
        raise ValueError(f"No scientific method version registered for {product!r}") from exc
