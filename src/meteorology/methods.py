"""Versioned scientific interpretations for durable meteorological products.

Change a method identifier whenever the meaning or numerical calculation of a
published value changes, even if its Arrow type is unchanged.
"""

from __future__ import annotations

METHOD_VERSIONS: dict[str, str] = {
    "meteorological.spatial_support": "h3_bbox_centroid_support_v1",
    "meteorological.surface_weather.download": "hrrr_f00_f01_nearest_sample_v1",
    "meteorological.surface_weather": "hrrr_surface_daily_v2",
    "meteorological.daylight": "daylight_astronomy_v2",
    "meteorological.lunar": "lunar_illumination_v2",
    "meteorological.daily_matrix": "native_resolution_daily_matrix_v1",
}


def method_version(product: str) -> str:
    """Return the registered method identity for a supported product."""

    try:
        return METHOD_VERSIONS[product]
    except KeyError as exc:
        raise ValueError(f"No scientific method version registered for {product!r}") from exc
