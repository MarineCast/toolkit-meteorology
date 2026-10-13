from __future__ import annotations

from typing import Any

from shapely.geometry import Polygon, mapping


def _load_h3() -> Any:
    try:
        import h3  # type: ignore
    except Exception as e:
        raise ImportError("Install h3-py (pip install h3) to use H3 operations.") from e
    return h3


def cell_to_boundary(cell: str) -> list[tuple[float, float]]:
    h3 = _load_h3()
    if hasattr(h3, "cell_to_boundary"):
        boundary = h3.cell_to_boundary(str(cell))
    elif hasattr(h3, "h3_to_geo_boundary"):
        boundary = h3.h3_to_geo_boundary(str(cell))
    else:
        raise ImportError("Unknown h3 API: expected cell_to_boundary or h3_to_geo_boundary")
    return [(float(lat), float(lng)) for lat, lng in boundary]


def cell_to_polygon(cell: str) -> Polygon:
    """Convert an H3 cell to a WGS84 Shapely polygon."""
    latlon = cell_to_boundary(str(cell))
    return Polygon([(lng, lat) for lat, lng in latlon])


def cell_to_latlng(cell: str) -> tuple[float, float]:
    h3 = _load_h3()
    if hasattr(h3, "cell_to_latlng"):
        lat, lng = h3.cell_to_latlng(str(cell))
        return float(lat), float(lng)
    if hasattr(h3, "h3_to_geo"):
        lat, lng = h3.h3_to_geo(str(cell))
        return float(lat), float(lng)
    raise ImportError("Unknown h3 API: expected cell_to_latlng or h3_to_geo")


def polygon_to_cells(geometry: Any, resolution: int) -> set[str]:
    """Return cells covering a polygon across h3-py v3/v4 APIs."""
    h3 = _load_h3()
    if hasattr(geometry, "geoms") and geometry.geom_type == "MultiPolygon":
        cells: set[str] = set()
        for part in geometry.geoms:
            cells.update(polygon_to_cells(part, resolution))
        return cells

    if hasattr(h3, "geo_to_cells"):
        try:
            return {str(x) for x in h3.geo_to_cells(geometry, int(resolution))}
        except TypeError:
            return {str(x) for x in h3.geo_to_cells(geometry.__geo_interface__, int(resolution))}

    geojson = mapping(geometry)
    if hasattr(h3, "polyfill_geojson"):
        return {str(x) for x in h3.polyfill_geojson(geojson, int(resolution))}
    if hasattr(h3, "polyfill"):
        return {
            str(x)
            for x in h3.polyfill(
                geojson,
                int(resolution),
                geo_json_conformant=True,
            )
        }
    raise ImportError("Unknown h3 API: expected geo_to_cells or polyfill")
