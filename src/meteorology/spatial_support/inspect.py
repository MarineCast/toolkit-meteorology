"""Inspect canonical meteorological H3 support in a toggle-layer HTML map."""

from __future__ import annotations

import argparse
from pathlib import Path

import folium
import geopandas as gpd

from meteorology.core.config.presentation import (
    DEFAULT_PRESENTATION_CONFIG_PATH,
    load_presentation_settings,
)
from meteorology.core.geo.h3 import cell_to_polygon

from ..artifacts import load_manifest
from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config
from ..inspection import manifest_info_html
from .build import load_meteorological_support


def inspect_meteorological_spatial_support(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    presentation_config_path: str | Path = DEFAULT_PRESENTATION_CONFIG_PATH,
    output_path: str | Path | None = None,
) -> Path:
    config = load_meteorological_config(config_path)
    manifest = load_manifest(config.support_manifest_path, verify_artifacts=True)
    settings = load_presentation_settings(presentation_config_path)
    destination = (
        Path(output_path).expanduser().resolve()
        if output_path
        else settings.export_path(
            Path("domains/environmental_layer/meteorological/spatial_support"),
            "spatial_support.html",
        )
    )
    map_ = folium.Map(
        location=[
            (config.bbox["min_lat"] + config.bbox["max_lat"]) / 2,
            (config.bbox["min_lon"] + config.bbox["max_lon"]) / 2,
        ],
        zoom_start=settings.default_zoom,
        tiles=settings.basemap_tile_layer,
        attr=settings.basemap_attribution or None,
    )
    map_.get_root().html.add_child(folium.Element(manifest_info_html(manifest)))
    colors = ["#348ABD", "#7A68A6", "#A60628"]
    for index, resolution in enumerate(config.support_resolutions):
        frame = load_meteorological_support(resolution, config_path)
        geometry = [cell_to_polygon(cell) for cell in frame["H3_INDEX"]]
        layer = gpd.GeoDataFrame(frame, geometry=geometry, crs="EPSG:4326")
        folium.GeoJson(
            layer[["H3_INDEX", "H3_RESOLUTION", "SUPPORT_STATE", "geometry"]],
            name=f"H3 R{resolution} ({len(layer):,} cells)",
            style_function=lambda _feature, color=colors[index % len(colors)]: {
                "color": color,
                "weight": 0.35,
                "fillColor": color,
                "fillOpacity": 0.12,
            },
            tooltip=folium.GeoJsonTooltip(fields=["H3_INDEX", "H3_RESOLUTION", "SUPPORT_STATE"]),
        ).add_to(map_)
    folium.LayerControl(collapsed=False).add_to(map_)
    destination.parent.mkdir(parents=True, exist_ok=True)
    map_.save(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--presentation-config", default=DEFAULT_PRESENTATION_CONFIG_PATH)
    parser.add_argument("--output-path")
    args = parser.parse_args()
    print(
        inspect_meteorological_spatial_support(
            args.config,
            presentation_config_path=args.presentation_config,
            output_path=args.output_path,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
