"""Build a compact interactive map of the canonical daily R5 weather history."""

from __future__ import annotations

import argparse
import base64
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
from shapely.geometry import mapping

from meteorology.core.artifacts import atomic_write_text
from meteorology.core.config.presentation import (
    DEFAULT_PRESENTATION_CONFIG_PATH,
    PresentationSettings,
    load_presentation_settings,
)
from meteorology.core.geo.h3 import cell_to_latlng, cell_to_polygon

from ..artifacts import load_manifest
from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config

MAP_EXPORT_SUBDIRECTORY = Path("domains/environmental_layer/meteorological/surface_weather")
TIME_SERIES_MAP_FILENAME = "surface_weather_time_series.html"


@dataclass(frozen=True)
class WeatherMetric:
    field: str
    label: str
    units: str
    decimals: int
    color_scale: str


WEATHER_METRICS: tuple[WeatherMetric, ...] = (
    WeatherMetric("TEMPERATURE_2M_C_MEAN", "Mean 2 m temperature", "°C", 1, "temperature"),
    WeatherMetric(
        "RELATIVE_HUMIDITY_2M_PCT_MEAN",
        "Mean 2 m relative humidity",
        "%",
        1,
        "percent",
    ),
    WeatherMetric("WIND_SPEED_10M_MS_MEAN", "Mean 10 m wind speed", "m/s", 2, "nonnegative"),
    WeatherMetric("WIND_GUST_SURFACE_MS_MEAN", "Mean surface wind gust", "m/s", 2, "nonnegative"),
    WeatherMetric("VISIBILITY_KM_MEAN", "Mean visibility", "km", 1, "nonnegative"),
    WeatherMetric("TOTAL_CLOUD_COVER_PCT_MEAN", "Mean total cloud cover", "%", 1, "percent"),
    WeatherMetric(
        "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN",
        "Mean sea-level pressure",
        "hPa",
        1,
        "pressure",
    ),
    WeatherMetric(
        "PRECIP_MM_DAY_ESTIMATE",
        "Daily precipitation estimate (f01 PRATE)",
        "mm",
        2,
        "precipitation",
    ),
)

_VALIDATION_COLUMNS = (
    "H3_INDEX",
    "DATE",
    "EXPECTED_SAMPLE_COUNT",
    "SAMPLE_COUNT",
    "SAMPLE_COVERAGE_FRAC",
    "QC_STATE",
)


def _all_true(value: pa.Array | pa.ChunkedArray) -> bool:
    result = pc.all(value)
    return bool(result.as_py()) if result.is_valid else False


def _validate_and_order_table(
    table: pa.Table,
    metrics: Sequence[WeatherMetric] = WEATHER_METRICS,
) -> tuple[pa.Table, list[str], list[str]]:
    """Fail closed, then return deterministic date-major/cell-minor rows."""

    required = {*_VALIDATION_COLUMNS, *(metric.field for metric in metrics)}
    missing = sorted(required.difference(table.column_names))
    if missing:
        raise ValueError(f"Weather time-series input is missing columns: {missing}")
    if table.num_rows == 0:
        raise ValueError("Weather time-series input is empty.")

    ordered = table.select(sorted(required)).sort_by(
        [("DATE", "ascending"), ("H3_INDEX", "ascending")]
    )
    dates = sorted(str(value) for value in pc.unique(ordered["DATE"]).to_pylist())
    cells = sorted(str(value) for value in pc.unique(ordered["H3_INDEX"]).to_pylist())
    if ordered.num_rows != len(dates) * len(cells):
        raise ValueError(
            "Weather time-series input does not contain the complete date-by-H3 support."
        )

    expected_cells = pa.chunked_array([pa.array(cells, type=pa.string())])
    for date_index, date in enumerate(dates):
        block = ordered.slice(date_index * len(cells), len(cells))
        if (
            str(block["DATE"][0].as_py()) != date
            or str(block["DATE"][-1].as_py()) != date
            or not block["H3_INDEX"].equals(expected_cells)
        ):
            raise ValueError(f"Weather time-series H3 support is incomplete for {date}.")

    parsed_dates = pd.to_datetime(dates, errors="raise")
    expected_dates = pd.date_range(parsed_dates[0], parsed_dates[-1], freq="D")
    if not parsed_dates.equals(expected_dates):
        raise ValueError("Weather time-series input has one or more missing calendar dates.")
    if not _all_true(pc.equal(ordered["QC_STATE"], "COMPLETE")):
        raise ValueError("Weather time-series input contains a non-COMPLETE QC state.")
    if not _all_true(pc.equal(ordered["SAMPLE_COUNT"], ordered["EXPECTED_SAMPLE_COUNT"])):
        raise ValueError("Weather time-series input contains an incomplete daily sample count.")
    if not _all_true(pc.equal(ordered["SAMPLE_COVERAGE_FRAC"], 1.0)):
        raise ValueError("Weather time-series input contains incomplete daily coverage.")
    for metric in metrics:
        values = ordered[metric.field]
        if values.null_count or not _all_true(pc.is_finite(values)):
            raise ValueError(
                f"Weather time-series input contains null or non-finite {metric.field} values."
            )
    return ordered, dates, cells


def _quantize(values: np.ndarray) -> dict[str, Any]:
    """Encode finite values as portable little-endian uint16 with bounded error."""

    numeric = np.asarray(values, dtype=np.float64)
    if numeric.size == 0 or not np.isfinite(numeric).all():
        raise ValueError("Only non-empty finite arrays can be quantized.")
    minimum = float(numeric.min())
    maximum = float(numeric.max())
    scale = (maximum - minimum) / 65535.0 if maximum > minimum else 1.0
    encoded = np.rint((numeric - minimum) / scale).astype("<u2", copy=False)
    return {
        "data": base64.b64encode(encoded.tobytes(order="C")).decode("ascii"),
        "offset": minimum,
        "scale": scale,
        "maximumQuantizationError": 0.0 if maximum == minimum else scale / 2.0,
    }


def _color_bounds(values: np.ndarray, kind: str) -> tuple[float, float]:
    numeric = np.asarray(values, dtype=np.float64)
    if kind == "percent":
        return 0.0, 100.0
    if kind in {"nonnegative", "precipitation"}:
        quantile = 0.995 if kind == "precipitation" else 0.99
        upper = float(np.quantile(numeric, quantile))
        return 0.0, upper if upper > 0.0 else 1.0
    lower = float(np.quantile(numeric, 0.01))
    upper = float(np.quantile(numeric, 0.99))
    if math.isclose(lower, upper):
        upper = lower + 1.0
    return lower, upper


def _tile_definition(settings: PresentationSettings) -> tuple[str, str]:
    name = settings.basemap_tile_layer.strip().lower()
    if name in {"cartodb positron", "cartodbpositron", "positron"}:
        return (
            "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
            settings.basemap_attribution or "&copy; OpenStreetMap contributors &copy; CARTO",
        )
    if name in {"openstreetmap", "open street map"}:
        return (
            "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            settings.basemap_attribution or "&copy; OpenStreetMap contributors",
        )
    if "{z}" in settings.basemap_tile_layer:
        return (
            settings.basemap_tile_layer,
            settings.basemap_attribution or "Basemap attribution unavailable",
        )
    raise ValueError(
        "The time-series map supports CartoDB positron, OpenStreetMap, or an explicit tile URL."
    )


def _geometry_payload(cells: Sequence[str]) -> tuple[dict[str, Any], list[float], int]:
    features: list[dict[str, Any]] = []
    centers: list[tuple[float, float]] = []
    for index, cell in enumerate(cells):
        geometry = mapping(cell_to_polygon(cell))
        geometry["coordinates"] = [
            [[round(float(lon), 6), round(float(lat), 6)] for lon, lat in ring]
            for ring in geometry["coordinates"]
        ]
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {"cellIndex": index, "h3": cell},
            }
        )
        centers.append(cell_to_latlng(cell))
    mean_lat = float(np.mean([lat for lat, _ in centers]))
    mean_lon = float(np.mean([lon for _, lon in centers]))
    selected_index = min(
        range(len(centers)),
        key=lambda index: (centers[index][0] - mean_lat) ** 2 + (centers[index][1] - mean_lon) ** 2,
    )
    return (
        {"type": "FeatureCollection", "features": features},
        [mean_lat, mean_lon],
        selected_index,
    )


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    source = next(iter(manifest.get("sources", [])), {})
    return {
        "runId": manifest.get("run_id"),
        "buildTimeUtc": manifest.get("build_time_utc"),
        "sourceCompleteness": manifest.get("source_completeness"),
        "sourceName": source.get("name", "NOAA HRRR surface analysis"),
        "attribution": source.get("attribution", "NOAA/NCEP HRRR"),
        "observationPeriod": source.get("observation_period"),
        "limitations": list(manifest.get("known_limitations", [])),
    }


def build_weather_time_series_payload(
    *,
    dataset_path: str | Path,
    manifest_path: str | Path,
    settings: PresentationSettings,
    initial_date: str | None = None,
) -> dict[str, Any]:
    """Load, verify, and compact the canonical daily weather product for the browser."""

    manifest = load_manifest(manifest_path, verify_artifacts=True)
    columns = [*_VALIDATION_COLUMNS, *(metric.field for metric in WEATHER_METRICS)]
    table = ds.dataset(Path(dataset_path), format="parquet", partitioning="hive").to_table(
        columns=columns
    )
    ordered, dates, cells = _validate_and_order_table(table)

    temporal = manifest.get("temporal_coverage", {})
    if temporal.get("start_date") != dates[0] or temporal.get("end_date") != dates[-1]:
        raise ValueError("Weather manifest temporal coverage does not match the daily product.")
    if int(manifest.get("h3_resolution", -1)) != 5:
        raise ValueError("Weather time-series map requires the canonical H3 R5 product.")
    if initial_date is not None and str(initial_date) not in dates:
        raise ValueError(f"Initial weather map date is unavailable: {initial_date}")

    geometry, center, selected_cell_index = _geometry_payload(cells)
    metric_payload: dict[str, dict[str, Any]] = {}
    for metric in WEATHER_METRICS:
        values = ordered[metric.field].combine_chunks().to_numpy(zero_copy_only=False)
        encoded = _quantize(values)
        color_min, color_max = _color_bounds(values, metric.color_scale)
        metric_payload[metric.field] = {
            "label": metric.label,
            "units": metric.units,
            "decimals": metric.decimals,
            "colorMin": color_min,
            "colorMax": color_max,
            **encoded,
        }

    tile_url, tile_attribution = _tile_definition(settings)
    return {
        "schemaVersion": 1,
        "generatedAtUtc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "dates": dates,
        "cells": cells,
        "cellCount": len(cells),
        "dateCount": len(dates),
        "geometry": geometry,
        "center": center,
        "selectedCellIndex": selected_cell_index,
        "initialDateIndex": dates.index(str(initial_date)) if initial_date else len(dates) - 1,
        "metricOrder": [metric.field for metric in WEATHER_METRICS],
        "metrics": metric_payload,
        "palette": list(settings.color_map()),
        "basemap": {"url": tile_url, "attribution": tile_attribution},
        "manifest": _manifest_summary(manifest),
    }


def inspect_surface_weather_time_series(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    presentation_config_path: str | Path = DEFAULT_PRESENTATION_CONFIG_PATH,
    output_path: str | Path | None = None,
    initial_date: str | None = None,
) -> Path:
    """Write a standalone interactive daily weather time-series map."""

    config = load_meteorological_config(config_path)
    settings = load_presentation_settings(presentation_config_path)
    payload = build_weather_time_series_payload(
        dataset_path=config.surface_weather.daily_output_dir,
        manifest_path=config.surface_weather.manifest_path,
        settings=settings,
        initial_date=initial_date,
    )
    destination = (
        Path(output_path).expanduser().resolve()
        if output_path
        else settings.export_path(MAP_EXPORT_SUBDIRECTORY, TIME_SERIES_MAP_FILENAME)
    )
    rendered = _HTML_TEMPLATE.replace(
        "__WEATHER_PAYLOAD__", json.dumps(payload, separators=(",", ":"), allow_nan=False)
    )
    return atomic_write_text(destination, rendered, overwrite=True)


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>OrcaCast Surface Weather Time Series</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
  <style>
    :root { --ink:#0c1c3a; --teal:#176f7d; --aqua:#38a9aa; --pale:#e8f4f1; --line:#cad8d7; --paper:#fff; }
    * { box-sizing:border-box; }
    html,body { height:100%; margin:0; color:var(--ink); font:14px/1.35 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#f5f8f8; }
    button,select,input { font:inherit; }
    button,select { border:1px solid #91aaa8; border-radius:6px; background:#fff; color:var(--ink); min-height:34px; }
    button { cursor:pointer; padding:0 11px; }
    button:hover,button:focus-visible,select:focus-visible,input:focus-visible { outline:3px solid rgba(56,169,170,.28); outline-offset:1px; }
    .app { height:100%; min-height:560px; display:grid; grid-template-rows:auto minmax(0,1fr) auto; }
    header { z-index:1001; display:flex; align-items:center; gap:18px; padding:10px 14px; background:rgba(255,255,255,.97); border-bottom:1px solid var(--line); box-shadow:0 2px 8px rgba(12,28,58,.08); }
    .title { min-width:250px; }
    h1 { margin:0; font-size:18px; letter-spacing:-.01em; }
    .subtitle { color:#526866; font-size:12px; margin-top:2px; }
    .metric-control { margin-left:auto; display:flex; align-items:center; gap:8px; }
    .metric-control label { font-weight:650; white-space:nowrap; }
    #metricSelect { max-width:310px; padding:0 30px 0 9px; }
    main { min-height:0; display:grid; grid-template-columns:minmax(0,1fr) 380px; }
    .map-shell { min-width:0; min-height:0; position:relative; }
    #map { width:100%; height:100%; min-height:360px; background:#dce8e6; }
    #loading { position:absolute; inset:0; z-index:1100; display:grid; place-items:center; background:rgba(232,244,241,.92); font-weight:700; letter-spacing:.01em; }
    .legend { position:absolute; z-index:700; left:12px; bottom:12px; width:min(270px,calc(100% - 24px)); padding:9px 10px; border:1px solid rgba(12,28,58,.18); border-radius:7px; background:rgba(255,255,255,.94); box-shadow:0 2px 10px rgba(12,28,58,.12); pointer-events:none; }
    .legend-title { font-size:12px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .legend-gradient { height:10px; margin:6px 0 3px; border-radius:5px; }
    .legend-range { display:flex; justify-content:space-between; color:#516765; font-size:11px; }
    aside { min-height:0; overflow:auto; padding:14px; border-left:1px solid var(--line); background:var(--paper); }
    aside h2 { margin:0 0 5px; font-size:16px; }
    .hint { margin:0 0 12px; color:#607472; font-size:12px; }
    .cell-id { padding:8px 9px; border-radius:6px; background:var(--pale); font:12px ui-monospace,SFMono-Regular,Menlo,monospace; word-break:break-all; }
    .current-reading { display:flex; align-items:baseline; gap:8px; margin:12px 0 7px; }
    .current-reading strong { font-size:25px; letter-spacing:-.03em; }
    .current-reading span { color:#5b706e; }
    .chart-wrap { position:relative; width:100%; min-height:230px; margin:4px 0 12px; border:1px solid var(--line); border-radius:7px; background:#fbfdfd; overflow:hidden; }
    #seriesChart { display:block; width:100%; height:230px; }
    .chart-label { fill:#526866; font-size:11px; }
    .chart-value { fill:var(--ink); font-size:11px; font-weight:700; }
    table { border-collapse:collapse; width:100%; font-size:12px; }
    th,td { padding:6px 5px; border-bottom:1px solid #e2ebea; text-align:left; }
    th { color:#526866; font-weight:600; }
    td:last-child { text-align:right; font-variant-numeric:tabular-nums; }
    tr.active { background:var(--pale); font-weight:700; }
    details { margin-top:13px; border-top:1px solid var(--line); padding-top:10px; font-size:12px; color:#526866; }
    summary { cursor:pointer; color:var(--ink); font-weight:700; }
    details ul { padding-left:18px; }
    footer { z-index:1001; padding:10px 14px 12px; border-top:1px solid var(--line); background:#fff; box-shadow:0 -2px 8px rgba(12,28,58,.06); }
    .timeline-top { display:grid; grid-template-columns:auto auto minmax(150px,1fr) auto 112px; align-items:center; gap:8px; }
    .date-readout { min-width:112px; font-weight:750; font-variant-numeric:tabular-nums; text-align:center; }
    #dateSlider { width:100%; accent-color:var(--aqua); cursor:ew-resize; }
    .range-labels { display:flex; justify-content:space-between; margin:2px 38px 0 89px; color:#657a78; font-size:11px; }
    .selected-grid { filter:drop-shadow(0 0 2px #fff); }
    .leaflet-tooltip { font-size:12px; }
    .leaflet-container { font:inherit; }
    @media (max-width:820px) {
      .app { height:auto; min-height:100%; grid-template-rows:auto auto auto; }
      header { align-items:flex-start; flex-wrap:wrap; gap:8px; }
      .metric-control { width:100%; margin-left:0; }
      #metricSelect { flex:1; max-width:none; }
      main { grid-template-columns:1fr; grid-template-rows:58vh auto; }
      aside { overflow:visible; border-left:0; border-top:1px solid var(--line); }
      footer { position:sticky; bottom:0; }
      .timeline-top { grid-template-columns:auto auto minmax(100px,1fr) auto; }
      .date-readout { grid-column:1 / -1; grid-row:1; }
      .range-labels { margin-left:0; margin-right:0; }
    }
  </style>
</head>
<body>
<div class="app">
  <header>
    <div class="title">
      <h1>Surface weather through time</h1>
      <div class="subtitle" id="coverageLabel">Strict direct-HRRR · H3 resolution 5</div>
    </div>
    <div class="metric-control">
      <label for="metricSelect">Variable</label>
      <select id="metricSelect" aria-label="Weather variable"></select>
    </div>
  </header>
  <main>
    <section class="map-shell" aria-label="Daily weather map">
      <div id="map"></div>
      <div id="loading" role="status">Decoding verified weather history…</div>
      <div class="legend" aria-label="Map color legend">
        <div class="legend-title" id="legendTitle"></div>
        <div class="legend-gradient" id="legendGradient"></div>
        <div class="legend-range"><span id="legendMin"></span><span id="legendMax"></span></div>
      </div>
    </section>
    <aside aria-label="Selected grid time series">
      <h2>Selected grid history</h2>
      <p class="hint">Click any H3 cell to inspect the active variable over the full period. Click the plot to jump to a date.</p>
      <div class="cell-id" id="selectedCell"></div>
      <div class="current-reading" aria-live="polite"><strong id="currentValue"></strong><span id="currentMetric"></span></div>
      <div class="chart-wrap">
        <svg id="seriesChart" viewBox="0 0 660 230" role="img" aria-label="Selected grid time series"></svg>
      </div>
      <table aria-label="Selected grid values for current date">
        <thead><tr><th>Variable</th><th id="tableDate"></th></tr></thead>
        <tbody id="valueTable"></tbody>
      </table>
      <details>
        <summary>Source and interpretation</summary>
        <p id="sourceSummary"></p>
        <ul id="limitations"></ul>
      </details>
    </aside>
  </main>
  <footer aria-label="Weather date controls">
    <div class="timeline-top">
      <button id="playButton" type="button" aria-label="Play dates">▶ Play</button>
      <button id="previousButton" type="button" aria-label="Previous date">‹</button>
      <input id="dateSlider" type="range" min="0" step="1" aria-label="Weather date">
      <button id="nextButton" type="button" aria-label="Next date">›</button>
      <div class="date-readout" id="dateReadout" aria-live="polite"></div>
    </div>
    <div class="range-labels"><span id="firstDate"></span><span>Pacific local date</span><span id="lastDate"></span></div>
  </footer>
</div>
<script type="application/json" id="weatherPayload">__WEATHER_PAYLOAD__</script>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(() => {
  'use strict';
  const node = id => document.getElementById(id);
  const payloadNode = node('weatherPayload');
  const weather = JSON.parse(payloadNode.textContent);
  const decoded = {};
  const littleEndian = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

  function decodeMetric(metric) {
    const binary = atob(metric.data);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    let raw;
    if (littleEndian) {
      raw = new Uint16Array(bytes.buffer);
    } else {
      raw = new Uint16Array(bytes.length / 2);
      const view = new DataView(bytes.buffer);
      for (let i = 0; i < raw.length; i += 1) raw[i] = view.getUint16(i * 2, true);
    }
    return { raw, offset: metric.offset, scale: metric.scale };
  }
  weather.metricOrder.forEach(field => {
    decoded[field] = decodeMetric(weather.metrics[field]);
    delete weather.metrics[field].data;
  });
  payloadNode.textContent = '';

  const state = {
    metric: weather.metricOrder[0],
    dateIndex: weather.initialDateIndex,
    cellIndex: weather.selectedCellIndex,
    playing: false,
    playTimer: null,
    chartHoverIndex: null,
    renderFrame: null
  };
  const paletteCss = weather.palette.join(',');
  const metricSelect = node('metricSelect');
  weather.metricOrder.forEach(field => {
    const option = document.createElement('option');
    option.value = field;
    option.textContent = `${weather.metrics[field].label} (${weather.metrics[field].units})`;
    metricSelect.appendChild(option);
  });
  node('dateSlider').max = String(weather.dateCount - 1);
  node('dateSlider').value = String(state.dateIndex);
  node('firstDate').textContent = weather.dates[0];
  node('lastDate').textContent = weather.dates[weather.dateCount - 1];
  node('coverageLabel').textContent = `Strict direct-HRRR · H3 R5 · ${weather.dateCount.toLocaleString()} dates · ${weather.cellCount.toLocaleString()} cells/day`;
  node('sourceSummary').textContent = `${weather.manifest.sourceName}; ${weather.manifest.attribution}. ${weather.manifest.observationPeriod || ''} Product state: ${weather.manifest.sourceCompleteness}.`;
  weather.manifest.limitations.forEach(value => {
    const item = document.createElement('li');
    item.textContent = value;
    node('limitations').appendChild(item);
  });

  const map = L.map('map', { preferCanvas:true, zoomControl:true, attributionControl:true });
  L.tileLayer(weather.basemap.url, { attribution:weather.basemap.attribution, maxZoom:19 }).addTo(map);
  const cellLayers = new Array(weather.cellCount);

  function rawValue(field, dateIndex, cellIndex) {
    const packed = decoded[field];
    return packed.offset + packed.raw[dateIndex * weather.cellCount + cellIndex] * packed.scale;
  }
  function formatValue(field, value) {
    const metric = weather.metrics[field];
    return `${Number(value).toLocaleString(undefined, {minimumFractionDigits:metric.decimals, maximumFractionDigits:metric.decimals})} ${metric.units}`;
  }
  function hexRgb(hex) {
    const value = hex.replace('#', '');
    return [parseInt(value.slice(0,2),16), parseInt(value.slice(2,4),16), parseInt(value.slice(4,6),16)];
  }
  function colorFor(value, metric) {
    const span = metric.colorMax - metric.colorMin || 1;
    const t = Math.max(0, Math.min(1, (value - metric.colorMin) / span));
    const position = t * (weather.palette.length - 1);
    const lower = Math.floor(position);
    const upper = Math.min(weather.palette.length - 1, lower + 1);
    const fraction = position - lower;
    const a = hexRgb(weather.palette[lower]);
    const b = hexRgb(weather.palette[upper]);
    const rgb = a.map((channel, index) => Math.round(channel + (b[index] - channel) * fraction));
    return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
  }
  function styleFor(cellIndex) {
    const metric = weather.metrics[state.metric];
    const selected = cellIndex === state.cellIndex;
    return {
      fillColor: colorFor(rawValue(state.metric, state.dateIndex, cellIndex), metric),
      fillOpacity: .82,
      color: selected ? '#0c1c3a' : '#496463',
      weight: selected ? 2.4 : .35,
      opacity: selected ? 1 : .72
    };
  }
  function tooltipHtml(cellIndex) {
    const metric = weather.metrics[state.metric];
    const value = rawValue(state.metric, state.dateIndex, cellIndex);
    return `<strong>${metric.label}</strong><br>${weather.dates[state.dateIndex]}<br>${formatValue(state.metric, value)}<br><span style="font-family:monospace">${weather.cells[cellIndex]}</span>`;
  }

  const gridLayer = L.geoJSON(weather.geometry, {
    style: feature => styleFor(feature.properties.cellIndex),
    onEachFeature: (feature, layer) => {
      const index = feature.properties.cellIndex;
      cellLayers[index] = layer;
      layer.bindTooltip(() => tooltipHtml(index), { sticky:true });
      layer.on('click', () => selectCell(index));
    }
  }).addTo(map);
  map.fitBounds(gridLayer.getBounds(), { padding:[12,12] });

  function renderLegend() {
    const metric = weather.metrics[state.metric];
    node('legendTitle').textContent = `${metric.label} (${metric.units})`;
    node('legendGradient').style.background = `linear-gradient(90deg,${paletteCss})`;
    node('legendMin').textContent = formatValue(state.metric, metric.colorMin);
    node('legendMax').textContent = formatValue(state.metric, metric.colorMax);
  }
  function renderMap() {
    cellLayers.forEach((layer, index) => {
      layer.setStyle(styleFor(index));
      if (layer.getTooltip()) layer.setTooltipContent(tooltipHtml(index));
    });
  }
  function renderTable() {
    const body = node('valueTable');
    body.replaceChildren();
    weather.metricOrder.forEach(field => {
      const metric = weather.metrics[field];
      const row = document.createElement('tr');
      if (field === state.metric) row.className = 'active';
      const label = document.createElement('td');
      label.textContent = metric.label;
      const value = document.createElement('td');
      value.textContent = formatValue(field, rawValue(field, state.dateIndex, state.cellIndex));
      row.append(label, value);
      row.addEventListener('click', () => { state.metric = field; metricSelect.value = field; scheduleRender(true); });
      body.appendChild(row);
    });
  }
  function chartSeries() {
    const values = new Float64Array(weather.dateCount);
    for (let index = 0; index < weather.dateCount; index += 1) {
      values[index] = rawValue(state.metric, index, state.cellIndex);
    }
    return values;
  }
  function svgElement(name, attributes = {}) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  }
  function renderChart() {
    const svg = node('seriesChart');
    svg.replaceChildren();
    const values = chartSeries();
    let minimum = Math.min(...values);
    let maximum = Math.max(...values);
    const padding = (maximum - minimum) * .08 || 1;
    minimum -= padding;
    maximum += padding;
    const left = 54, right = 648, top = 18, bottom = 194;
    const x = index => left + index / Math.max(1, weather.dateCount - 1) * (right - left);
    const y = value => bottom - (value - minimum) / (maximum - minimum) * (bottom - top);
    [top, (top + bottom) / 2, bottom].forEach((position, index) => {
      svg.appendChild(svgElement('line', {x1:left,x2:right,y1:position,y2:position,stroke:'#dce7e6','stroke-width':1}));
      const label = svgElement('text', {x:left-7,y:position+4,'text-anchor':'end',class:'chart-label'});
      label.textContent = (maximum - index * (maximum - minimum) / 2).toFixed(weather.metrics[state.metric].decimals);
      svg.appendChild(label);
    });
    let path = '';
    for (let index = 0; index < values.length; index += 1) path += `${index ? 'L' : 'M'}${x(index).toFixed(2)},${y(values[index]).toFixed(2)}`;
    svg.appendChild(svgElement('path', {d:path,fill:'none',stroke:'#176f7d','stroke-width':1.7,'vector-effect':'non-scaling-stroke'}));
    const cursorIndex = state.chartHoverIndex ?? state.dateIndex;
    const cursorX = x(cursorIndex);
    svg.appendChild(svgElement('line', {x1:cursorX,x2:cursorX,y1:top,y2:bottom,stroke:'#0c1c3a','stroke-width':1.2,'stroke-dasharray':'4 3'}));
    svg.appendChild(svgElement('circle', {cx:cursorX,cy:y(values[cursorIndex]),r:4.2,fill:'#fff',stroke:'#0c1c3a','stroke-width':2}));
    const valueLabel = svgElement('text', {x:Math.min(right-5,Math.max(left+5,cursorX)),y:12,'text-anchor':cursorX > (left+right)/2 ? 'end' : 'start',class:'chart-value'});
    valueLabel.textContent = `${weather.dates[cursorIndex]} · ${formatValue(state.metric, values[cursorIndex])}`;
    svg.appendChild(valueLabel);
    const startLabel = svgElement('text', {x:left,y:215,class:'chart-label'}); startLabel.textContent = weather.dates[0]; svg.appendChild(startLabel);
    const endLabel = svgElement('text', {x:right,y:215,'text-anchor':'end',class:'chart-label'}); endLabel.textContent = weather.dates[weather.dateCount-1]; svg.appendChild(endLabel);
    const interaction = svgElement('rect', {x:left,y:top,width:right-left,height:bottom-top,fill:'transparent',tabindex:0,'aria-label':'Time-series plot; click to select date'});
    interaction.addEventListener('pointermove', event => {
      const bounds = svg.getBoundingClientRect();
      const svgX = (event.clientX - bounds.left) / bounds.width * 660;
      state.chartHoverIndex = Math.max(0, Math.min(weather.dateCount-1, Math.round((svgX-left)/(right-left)*(weather.dateCount-1))));
      renderChart();
    });
    interaction.addEventListener('pointerleave', () => { state.chartHoverIndex = null; renderChart(); });
    interaction.addEventListener('click', () => {
      if (state.chartHoverIndex !== null) setDate(state.chartHoverIndex);
    });
    svg.appendChild(interaction);
  }
  function selectCell(index) {
    const previous = state.cellIndex;
    state.cellIndex = index;
    if (cellLayers[previous]) cellLayers[previous].setStyle(styleFor(previous));
    if (cellLayers[index]) { cellLayers[index].setStyle(styleFor(index)); cellLayers[index].bringToFront(); }
    renderDetails();
  }
  function renderDetails() {
    const metric = weather.metrics[state.metric];
    const value = rawValue(state.metric, state.dateIndex, state.cellIndex);
    node('selectedCell').textContent = weather.cells[state.cellIndex];
    node('currentValue').textContent = formatValue(state.metric, value);
    node('currentMetric').textContent = `${metric.label} · ${weather.dates[state.dateIndex]}`;
    node('tableDate').textContent = weather.dates[state.dateIndex];
    renderChart();
    renderTable();
  }
  function renderAll() {
    state.renderFrame = null;
    node('dateReadout').textContent = weather.dates[state.dateIndex];
    node('dateSlider').value = String(state.dateIndex);
    renderLegend();
    renderMap();
    renderDetails();
  }
  function scheduleRender(metricChanged = false) {
    if (metricChanged) state.chartHoverIndex = null;
    if (state.renderFrame !== null) cancelAnimationFrame(state.renderFrame);
    state.renderFrame = requestAnimationFrame(renderAll);
  }
  function setDate(index) {
    state.dateIndex = Math.max(0, Math.min(weather.dateCount - 1, Number(index)));
    scheduleRender(false);
  }
  function stopPlayback() {
    state.playing = false;
    if (state.playTimer !== null) window.clearInterval(state.playTimer);
    state.playTimer = null;
    node('playButton').textContent = '▶ Play';
    node('playButton').setAttribute('aria-label', 'Play dates');
  }
  function togglePlayback() {
    if (state.playing) { stopPlayback(); return; }
    state.playing = true;
    node('playButton').textContent = '❚❚ Pause';
    node('playButton').setAttribute('aria-label', 'Pause dates');
    state.playTimer = window.setInterval(() => {
      setDate(state.dateIndex >= weather.dateCount - 1 ? 0 : state.dateIndex + 1);
    }, 350);
  }

  metricSelect.addEventListener('change', event => { state.metric = event.target.value; scheduleRender(true); });
  node('dateSlider').addEventListener('input', event => { stopPlayback(); setDate(event.target.value); });
  node('previousButton').addEventListener('click', () => { stopPlayback(); setDate(state.dateIndex - 1); });
  node('nextButton').addEventListener('click', () => { stopPlayback(); setDate(state.dateIndex + 1); });
  node('playButton').addEventListener('click', togglePlayback);
  document.addEventListener('keydown', event => {
    if (event.target === metricSelect || event.target === node('dateSlider')) return;
    if (event.key === 'ArrowLeft') { stopPlayback(); setDate(state.dateIndex - 1); }
    if (event.key === 'ArrowRight') { stopPlayback(); setDate(state.dateIndex + 1); }
  });
  window.addEventListener('resize', () => { map.invalidateSize(); renderChart(); });

  renderAll();
  node('loading').remove();
})();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--presentation-config", default=DEFAULT_PRESENTATION_CONFIG_PATH)
    parser.add_argument("--output-path")
    parser.add_argument("--initial-date")
    args = parser.parse_args()
    print(
        inspect_surface_weather_time_series(
            args.config,
            presentation_config_path=args.presentation_config,
            output_path=args.output_path,
            initial_date=args.initial_date,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
