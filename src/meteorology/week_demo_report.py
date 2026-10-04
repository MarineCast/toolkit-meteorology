"""Render an inspectable Markdown companion from a completed live demo."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .artifacts import resolve_portable_path
from .validation import validate_product


def write_demo_report(output: Path) -> Path:
    report = json.loads((output / "REPORT.json").read_text())
    if report["status"] != "COMPLETE":
        raise ValueError("A completed demo is required before rendering its results.")
    manifest = output / "summary-both/MANIFEST.json"
    validate_product(manifest)
    frame = pd.read_parquet(output / "summary-both/weather-summary.parquet")
    native_bytes, hourly_rows = 0, 0
    import pyarrow.parquet as pq

    for day in report["daily"]:
        path = output / day["manifest"]
        payload = json.loads(path.read_text())
        directory = resolve_portable_path(payload["artifacts"][0]["path"], base=path.parent)
        for parquet in directory.rglob("*.parquet"):
            native_bytes += parquet.stat().st_size
            hourly_rows += pq.ParquetFile(parquet).metadata.num_rows
    peak = report.get("peak_rss_mib_all_invocations", report["peak_rss_mib_this_process"])
    lines = ["# Live HRRR week: measured results", "",
        f"Period: **{report['start_date']}–{report['end_date']}**, {report['timezone']}. "
        f"**{report['h3_cells']} H3 R5 cells**, **{hourly_rows:,} hourly rows**. "
        "All seven daily releases and both summary products passed validation.", "",
        "Source: NOAA/NCEP HRRR sfc/f00 analyses; nearest native-grid samples. "
        "These are retrospective environmental conditions, not observed species presence "
        "or as-issued weather forecasts.", "", "## Resources", "",
        "| Measurement | Result |", "| --- | ---: |",
        f"| Elapsed, all recorded invocations | {report['elapsed_seconds_all_invocations']:.1f} s |",
        f"| Peak process RSS | {peak:.1f} MiB |",
        f"| HTTP requests reserved | {report['transfer_requests']:,} |",
        f"| Received response payload | {report['received_payload_bytes'] / 1024**3:.3f} GiB |",
        f"| Reserved response payload | {report['reserved_payload_bytes'] / 1024**3:.3f} GiB |",
        f"| Seven hourly Parquets | {native_bytes / 1024**2:.3f} MiB |"]
    for scope, item in report["summaries"].items():
        lines.append(f"| {scope} summary Parquet ({item['rows']:,} rows) | {item['parquet_bytes'] / 1024**2:.3f} MiB |")
    for category, size in report["storage_bytes"].items():
        lines.append(f"| {category.replace('_', ' ')} | {size / 1024**2:.3f} MiB |")
    metadata_bytes = sum(p.stat().st_size for p in output.iterdir() if p.is_file()
                         and p.name != "REPORT.md")
    retained = sum(report["storage_bytes"].values()) + metadata_bytes
    lines += [f"| Run metadata, excluding this Markdown report | {metadata_bytes / 1024**2:.3f} MiB |",
        f"| Total retained, excluding this Markdown report | {retained / 1024**2:.3f} MiB |", "",
        "Parquet sizes exclude manifests and source evidence; the storage categories include them. "
        "The summary directories add storage. No raw source retention policy was applied. "
        "GRIB messages cover the native CONUS grid before cropping. Payload accounting excludes "
        "HTTP/TLS overhead. Peak RSS includes decoder, validation and publication libraries.", "",
        "## Daily regional means", "",
        "Equal weight per H3 centroid-hour across the configured land-and-water box. "
        "These are sampled means, not area-weighted regional observations.", "",
        "| Local date | Air temperature (°C) | Wind speed (m/s) | Cloud (%) | Visibility (km) |",
        "| --- | ---: | ---: | ---: | ---: |"]
    regional = frame.query("SPATIAL_SCOPE == 'region' and PERIOD == 'day'")
    pivot = regional.pivot(index="LOCAL_START_DATE", columns="METRIC", values="SAMPLED_MEAN")
    metrics = ["TEMPERATURE_2M_C", "WIND_SPEED_10M_MS", "TOTAL_CLOUD_COVER_PCT", "VISIBILITY_KM"]
    for day, row in pivot.iterrows():
        lines.append(f"| {day} | " + " | ".join(f"{row[m]:.2f}" for m in metrics) + " |")
    lines += ["", "## Spatial variation over the week", "",
        "The H3 range below is the minimum–maximum of individual cells' **weekly means**, "
        "not the range of instantaneous weather. It describes spatial variation, not forecast "
        "error or uncertainty. A regional mean cannot reproduce local conditions when this "
        "range is wide; whether that matters for ecological forecasting needs evaluation.", "",
        "| Metric | Unit | Regional weekly mean | H3 weekly mean minimum | H3 weekly mean maximum |",
        "| --- | --- | ---: | ---: | ---: |"]
    weekly = frame.query("PERIOD == 'week'")
    for metric, rows in weekly.groupby("METRIC", sort=True):
        native = rows.loc[rows.SPATIAL_SCOPE == "h3", "SAMPLED_MEAN"]
        region = rows.loc[rows.SPATIAL_SCOPE == "region"].iloc[0]
        lines.append(f"| {metric} | {region.UNIT} | {region.SAMPLED_MEAN:.3f} | {native.min():.3f} | {native.max():.3f} |")
    lines += ["", "## Acceptance boundaries", "",
        "- Implementation and real-source compatibility: passed for these cycles and bounds.",
        "- Independent observational accuracy, multi-season regional acceptance and future forecast skill: **NOT_RUN**.",
        "- Precipitation, astronomy and OrcaCast integration: outside this demo.",
        "- Availability is an assumed fixed lag; retrieval time is recorded separately.", "",
        "Exact checksums, invocation details, source manifests and counters are retained in "
        "`REPORT.json`, `TRANSFER.json` and the referenced daily/summary manifests.", ""]
    path = output / "REPORT.md"
    path.write_text("\n".join(lines))
    return path
