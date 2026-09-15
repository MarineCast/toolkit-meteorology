"""Shared manifest-aware HTML mapping utilities for meteorological products."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Sequence

import branca.colormap as cm
import folium
import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from meteorology.core.config.presentation import (
    DEFAULT_PRESENTATION_CONFIG_PATH,
    load_presentation_settings,
)
from meteorology.core.geo.h3 import cell_to_polygon

from .artifacts import load_manifest


def available_dates(dataset_path: str | Path) -> list[str]:
    root = Path(dataset_path)
    dates = sorted(
        path.name.split("=", 1)[1] for path in root.glob("year=*/date=*") if "=" in path.name
    )
    if dates:
        return dates
    table = ds.dataset(root, format="parquet").to_table(columns=["DATE"])
    return sorted({str(value) for value in table["DATE"].to_pylist()})


def read_product_date(dataset_path: str | Path, date: str) -> pd.DataFrame:
    dataset = ds.dataset(Path(dataset_path), format="parquet", partitioning="hive")
    table = dataset.to_table(filter=ds.field("DATE") == str(date))
    if table.num_rows == 0:
        raise ValueError(f"No meteorological rows are available for {date}: {dataset_path}")
    frame = table.to_pandas()
    if frame.duplicated(["H3_INDEX", "DATE"]).any():
        raise ValueError(f"Inspector input has duplicate H3/date keys: {dataset_path}")
    return frame.sort_values("H3_INDEX").reset_index(drop=True)


def manifest_info_html(manifest: dict[str, object]) -> str:
    """Render source, availability, licensing, and limitation metadata."""

    source_lines = []
    for source in manifest.get("sources", []):
        source_lines.append(
            "<li><strong>{}</strong><br>Attribution: {}<br>License: {}<br>"
            "Observation period: {}<br>Redistribution: {}</li>".format(
                escape(str(source.get("name", "unknown"))),
                escape(str(source.get("attribution", "unknown"))),
                escape(str(source.get("license", "unknown"))),
                escape(str(source.get("observation_period", "unknown"))),
                escape(str(source.get("redistribution_restrictions", "unknown"))),
            )
        )
    limitations = "".join(
        f"<li>{escape(str(value))}</li>" for value in manifest.get("known_limitations", [])
    )
    availability = escape(
        json.dumps(manifest.get("availability_semantics", {}), sort_keys=True, default=str)
    )
    return f"""
    <div style='position:fixed;top:52px;right:12px;z-index:9999;background:white;
                padding:10px;border:1px solid #777;border-radius:4px;width:390px;
                max-height:42vh;overflow:auto;font-size:11px;line-height:1.35'>
      <strong>Product contract</strong><br>
      Completeness: {escape(str(manifest.get('source_completeness', 'unknown')))}<br>
      Code: {escape(str(manifest.get('code_revision') or 'uncommitted'))}
      {' (dirty)' if manifest.get('code_dirty') else ''}<br>
      Availability: {availability}<br>
      <strong>Sources</strong><ul>{''.join(source_lines)}</ul>
      <strong>Limitations and warnings</strong><ul>{limitations or '<li>None declared</li>'}</ul>
    </div>
    """


def inspect_daily_product(
    *,
    dataset_path: str | Path,
    manifest_path: str | Path,
    output_path: str | Path,
    date: str,
    title: str,
    metrics: Sequence[str],
    metadata: Sequence[str] = (),
    categorical_layers: Sequence[str] = (),
    presentation_config_path: str | Path = DEFAULT_PRESENTATION_CONFIG_PATH,
) -> Path:
    """Validate a product manifest and render selected columns as H3 layers."""

    manifest = load_manifest(manifest_path, verify_artifacts=True)
    frame = read_product_date(dataset_path, date)
    missing = sorted(set(metrics).difference(frame.columns))
    if missing:
        raise ValueError(f"Inspector metrics are missing: {missing}")
    settings = load_presentation_settings(presentation_config_path)
    center = [
        float(frame.get("CENTROID_LAT", pd.Series([48.5])).mean()),
        float(frame.get("CENTROID_LON", pd.Series([-123.3])).mean()),
    ]
    options = {
        "location": center,
        "zoom_start": settings.default_zoom,
        "tiles": settings.basemap_tile_layer,
    }
    if settings.basemap_attribution:
        options["attr"] = settings.basemap_attribution
    map_ = folium.Map(**options)
    title_html = f"<h3 style='position:fixed;top:8px;left:52px;z-index:9999'>{title} — {date}</h3>"
    map_.get_root().html.add_child(folium.Element(title_html))
    map_.get_root().html.add_child(folium.Element(manifest_info_html(manifest)))
    tooltip_columns = ["H3_INDEX", *[value for value in metadata if value in frame.columns]]
    geometries = [cell_to_polygon(cell) for cell in frame["H3_INDEX"].astype(str)]
    for layer_index, metric in enumerate(metrics):
        values = pd.to_numeric(frame[metric], errors="coerce")
        finite = values[np.isfinite(values)]
        minimum = float(finite.min()) if len(finite) else 0.0
        maximum = float(finite.max()) if len(finite) else 1.0
        if minimum == maximum:
            maximum = minimum + 1.0
        colormap = cm.linear.viridis.scale(minimum, maximum)
        colormap.caption = metric
        records = []
        for row_index, (_, row) in enumerate(frame.iterrows()):
            properties = {column: row[column] for column in tooltip_columns}
            value = values.iloc[row_index]
            properties[metric] = None if pd.isna(value) else float(value)
            records.append(
                {
                    "type": "Feature",
                    "geometry": geometries[row_index].__geo_interface__,
                    "properties": properties,
                }
            )

        def style(feature, color_scale=colormap, field=metric):
            value = feature["properties"].get(field)
            return {
                "color": "#555555",
                "weight": 0.25,
                "fillColor": "#bdbdbd" if value is None else color_scale(value),
                "fillOpacity": 0.72,
            }

        layer = folium.FeatureGroup(name=metric, show=layer_index == 0)
        folium.GeoJson(
            {"type": "FeatureCollection", "features": records},
            style_function=style,
            tooltip=folium.GeoJsonTooltip(fields=[*tooltip_columns, metric]),
        ).add_to(layer)
        layer.add_to(map_)
        if layer_index == 0:
            colormap.add_to(map_)
    categorical_palette = [
        "#1b9e77",
        "#d95f02",
        "#7570b3",
        "#e7298a",
        "#66a61e",
        "#e6ab02",
        "#a6761d",
        "#666666",
    ]
    for categorical_index, field in enumerate(categorical_layers):
        if field not in frame.columns:
            raise ValueError(f"Inspector categorical layer is missing: {field}")
        categories = sorted({str(value) for value in frame[field].dropna().unique()})
        colors = {
            category: categorical_palette[index % len(categorical_palette)]
            for index, category in enumerate(categories)
        }
        records = []
        for row_index, (_, row) in enumerate(frame.iterrows()):
            properties = {column: row[column] for column in tooltip_columns}
            properties[field] = None if pd.isna(row[field]) else str(row[field])
            records.append(
                {
                    "type": "Feature",
                    "geometry": geometries[row_index].__geo_interface__,
                    "properties": properties,
                }
            )

        def categorical_style(feature, palette=colors, category_field=field):
            value = feature["properties"].get(category_field)
            return {
                "color": "#555555",
                "weight": 0.25,
                "fillColor": palette.get(value, "#bdbdbd"),
                "fillOpacity": 0.72,
            }

        layer = folium.FeatureGroup(
            name=f"{field} ({', '.join(categories)})",
            show=not metrics and categorical_index == 0,
        )
        folium.GeoJson(
            {"type": "FeatureCollection", "features": records},
            style_function=categorical_style,
            tooltip=folium.GeoJsonTooltip(fields=[*tooltip_columns, field]),
        ).add_to(layer)
        layer.add_to(map_)
    folium.LayerControl(collapsed=False).add_to(map_)
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    map_.save(destination)
    return destination
