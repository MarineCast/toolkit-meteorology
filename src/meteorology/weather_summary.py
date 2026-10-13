"""Stream validated hourly releases into compact daily and weekly context."""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import h3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .artifacts import (
    cell_set_hash, checksum_path, family_publication_parent, load_manifest, manifest_payload,
    parquet_contract, resolve_portable_path, write_manifest,
)
from .core.artifacts import TransactionalFamilyPublisher
from .core.data.meteorological_schemas import WEATHER_SUMMARY_SCHEMA as SCHEMA
from .hourly_weather.product import HOURLY_PRODUCT, validate_hourly_product
from .field_contracts import HARD_LIMITS
from .methods import method_version
from .temporal_products import HOURLY_CORE, _utc
from .variables import WEATHER_SAMPLE

PRODUCT = "meteorological.weather_summary"



def _bounds(day: date, period: str, zone: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(zone)
    return tuple(datetime.combine(d, time.min, tz).astimezone(timezone.utc)
                 for d in (day, day + timedelta(days=1 if period == "day" else 7)))


def _empty(cells: int) -> dict:
    shape = (cells, len(HOURLY_CORE))
    return dict(total=np.zeros(shape), minimum=np.full(shape, np.inf),
                maximum=np.full(shape, -np.inf), count=np.zeros(cells, dtype=np.int64),
                available=[None] * cells)


def _accumulate(state: dict, frame: pd.DataFrame, cells: list[str]) -> None:
    indices = {cell: i for i, cell in enumerate(cells)}
    for cell, group in frame.groupby("H3_INDEX", sort=False):
        index = indices[cell]
        values = group[list(HOURLY_CORE)].to_numpy(dtype=float)
        state["total"][index] += values.sum(axis=0)
        state["minimum"][index] = np.minimum(state["minimum"][index], values.min(axis=0))
        state["maximum"][index] = np.maximum(state["maximum"][index], values.max(axis=0))
        state["count"][index] += len(group)
        available = pd.to_datetime(group.AVAILABLE_AT_UTC, utc=True).max().to_pydatetime()
        previous = state["available"][index]
        state["available"][index] = max(previous, available) if previous else available


def _merge(target: dict, source: dict) -> None:
    target["total"] += source["total"]
    target["minimum"] = np.minimum(target["minimum"], source["minimum"])
    target["maximum"] = np.maximum(target["maximum"], source["maximum"])
    target["count"] += source["count"]
    for i, available in enumerate(source["available"]):
        if available is not None:
            previous = target["available"][i]
            target["available"][i] = max(previous, available) if previous else available


def _rows(state: dict, cells: list[str], day: date, period: str,
          zone: str, scope: str) -> list[dict]:
    start, end = _bounds(day, period, zone)
    hours = int((end - start).total_seconds() / 3600)
    selections = []
    if scope in {"h3", "both"}:
        selections.extend(("h3", cell, [i]) for i, cell in enumerate(cells))
    if scope in {"region", "both"}:
        selections.append(("region", None, list(range(len(cells)))))
    rows = []
    for spatial, cell, indices in selections:
        count = int(state["count"][indices].sum())
        expected = hours * len(indices)
        available = [state["available"][i] for i in indices if state["available"][i] is not None]
        for j, metric in enumerate(HOURLY_CORE):
            rows.append(dict(
                PERIOD=period, LOCAL_START_DATE=day.isoformat(), TIMEZONE=zone,
                PERIOD_START_UTC=start, PERIOD_END_UTC=end, SPATIAL_SCOPE=spatial,
                H3_INDEX=cell, METRIC=metric, UNIT=WEATHER_SAMPLE[metric][1],
                SAMPLED_MEAN=float(state["total"][indices, j].sum() / count) if count else None,
                SAMPLED_MIN=float(state["minimum"][indices, j].min()) if count else None,
                SAMPLED_MAX=float(state["maximum"][indices, j].max()) if count else None,
                VALID_CELL_HOURS=count, EXPECTED_CELL_HOURS=expected,
                COVERAGE_FRACTION=count / expected,
                STATUS="COMPLETE" if count == expected else "PARTIAL" if count else "UNAVAILABLE",
                AVAILABLE_AT_UTC=max(available) if available else None,
            ))
    return rows


def export_weather_summary(manifest_paths: list[str | Path], output_dir: str | Path, *,
                           spatial_scope: str = "both", as_of_utc: str | None = None) -> Path:
    """Export nine atmospheric metrics without retaining a multi-day frame in memory."""
    if spatial_scope not in {"h3", "region", "both"} or not manifest_paths:
        raise ValueError("Provide hourly manifests and spatial scope h3, region or both.")
    cutoff = _utc(as_of_utc) if as_of_utc is not None else None
    paths = [Path(p).resolve() for p in manifest_paths]
    parents = []
    for path in paths:
        preview = json.loads(path.read_text())
        if preview.get("product") != HOURLY_PRODUCT:
            raise ValueError("Weather summaries require published hourly weather releases.")
        parent = family_publication_parent(path, preview)
        if (parent / ".publication.lock").exists():
            parents.append(parent)
    output = Path(output_dir).resolve()
    # Pin every mutable source family until terminal publication. Frozen inputs
    # have no producer lock and remain checksum-verified.
    with TransactionalFamilyPublisher.read_locks(parents):
        entries = []
        for path in paths:
            manifest = load_manifest(path, verify_artifacts=False)
            entries.append((date.fromisoformat(manifest["resolved_config"]["local_date"]), path, manifest))
        entries.sort(key=lambda entry: entry[0])
        days = [entry[0] for entry in entries]
        if len(set(days)) != len(days) or (days[-1] - days[0]).days + 1 != len(days):
            raise ValueError("Hourly releases must cover unique contiguous local dates.")
        first = entries[0][2]
        policy_keys = ("timezone", "h3_resolution", "support_cell_set_hash", "availability_policy",
                       "availability_lag_hours", "source_evidence_kind")
        settings = {key: first["resolved_config"][key] for key in policy_keys}
        for _, _, manifest in entries:
            if (any(manifest["resolved_config"][key] != settings[key] for key in policy_keys)
                    or manifest["spatial_bounds_wgs84"] != first["spatial_bounds_wgs84"]
                    or manifest["method_version"] != first["method_version"]):
                raise ValueError("Hourly releases have incompatible support, source or time policies.")
        destination, terminal = output / "weather-summary.parquet", output / "MANIFEST.json"
        with TransactionalFamilyPublisher(output) as publisher:
            if destination.exists() or terminal.exists():
                raise FileExistsError("Summary output already exists; choose a new output directory.")
            staged = publisher.stage_path(destination)
            staged.parent.mkdir(parents=True, exist_ok=True)
            inputs, sources, cells = [], [], None
            week, weekly = None, None
            with pq.ParquetWriter(staged, SCHEMA, compression="zstd") as writer:
                for day, path, _ in entries:
                    validate_hourly_product(path)
                    manifest = load_manifest(path, verify_artifacts=False)
                    artifact = resolve_portable_path(manifest["artifacts"][0]["path"], base=path.parent)
                    files = sorted(artifact.rglob("*.parquet"))
                    frame = pq.ParquetFile(files[0]).read(columns=[
                        "H3_INDEX", "AVAILABLE_AT_UTC", *HOURLY_CORE,
                    ]).to_pandas()
                    current_cells = sorted(frame.H3_INDEX.unique())
                    if cells is not None and cells != current_cells:
                        raise ValueError("Hourly release cell identities differ.")
                    cells = current_cells
                    next_week = day - timedelta(days=day.weekday())
                    if week != next_week:
                        if weekly is not None:
                            writer.write_table(pa.Table.from_pylist(
                                _rows(weekly, cells, week, "week", settings["timezone"], spatial_scope), schema=SCHEMA))
                        week, weekly = next_week, _empty(len(cells))
                    if cutoff is not None:
                        frame = frame.loc[pd.to_datetime(frame.AVAILABLE_AT_UTC, utc=True) <= cutoff]
                    daily = _empty(len(cells))
                    _accumulate(daily, frame, cells)
                    _merge(weekly, daily)
                    writer.write_table(pa.Table.from_pylist(
                        _rows(daily, cells, day, "day", settings["timezone"], spatial_scope), schema=SCHEMA))
                    inputs.extend([
                        dict(role="hourly_manifest", path=str(path), checksum=checksum_path(path),
                             release_id=manifest["release_id"]),
                        dict(role="hourly_values", path=str(artifact), checksum=manifest["artifacts"][0]["checksum"]),
                    ])
                    sources.extend(source for source in manifest["sources"] if source not in sources)
                writer.write_table(pa.Table.from_pylist(
                    _rows(weekly, cells, week, "week", settings["timezone"], spatial_scope), schema=SCHEMA))
            resolved = {**settings, "spatial_scope": spatial_scope, "h3_cells": cells,
                        "as_of_utc": cutoff.isoformat() if cutoff else None,
                        "spatial_weighting": "equal_h3_centroid_samples",
                        "metrics": list(HOURLY_CORE), "schema_version": "weather-summary-v1"}
            coverage = dict(start_date=days[0].isoformat(), end_date=days[-1].isoformat())
            _validate_table(staged, resolved, coverage)
            for item in inputs:
                if checksum_path(item["path"]) != item["checksum"]:
                    raise ValueError("Hourly input changed during summary export.")
            payload = manifest_payload(
                product=PRODUCT, run_id=f"summary-{uuid.uuid4().hex[:12]}",
                config_path=entries[0][1], resolved_config=resolved,
                artifacts=[parquet_contract(staged, published_path=destination)],
                inputs=inputs, sources=sources, h3_resolution=5,
                spatial_bounds=first["spatial_bounds_wgs84"], temporal_coverage=coverage,
                availability_semantics=first["availability_semantics"],
                units={name: WEATHER_SAMPLE[name][1] for name in HOURLY_CORE},
                formulas={"SAMPLED_MEAN": "sum of contributing hourly centroid samples / VALID_CELL_HOURS"},
                limitations=["Retrospective f00 context; not a future weather forecast.",
                             "Regional statistics use equal centroid samples, not area means.",
                             "Edge weeks and as-of windows can be partial; inspect coverage.",
                             "Source validation is performed at export; summary validation checks lineage checksums and summary contracts.",
                             "No precipitation or astronomy in this product."],
            )
            write_manifest(publisher.stage_manifest_path(terminal), payload)
            publisher.publish()
    return terminal


def _validate_table(path: Path, settings: dict, coverage: dict) -> int:
    parquet = pq.ParquetFile(path)
    if not parquet.schema_arrow.equals(SCHEMA, check_metadata=True):
        raise ValueError("Weather summary Arrow schema differs.")
    days = pd.date_range(coverage["start_date"], coverage["end_date"]).date
    periods = {("day", d) for d in days} | {("week", d - timedelta(days=d.weekday())) for d in days}
    cells = settings["h3_cells"]
    scopes = [("h3", cell) for cell in cells] if settings["spatial_scope"] in {"h3", "both"} else []
    if settings["spatial_scope"] in {"region", "both"}:
        scopes.append(("region", None))
    expected_keys = {(scope, cell, metric) for scope, cell in scopes for metric in HOURLY_CORE}
    seen, total = set(), 0
    cutoff = _utc(settings["as_of_utc"]) if settings["as_of_utc"] else None
    for i in range(parquet.num_row_groups):
        rows = parquet.read_row_group(i).to_pylist()
        if not rows:
            raise ValueError("Empty weather summary row group.")
        period, day = rows[0]["PERIOD"], date.fromisoformat(rows[0]["LOCAL_START_DATE"])
        identity = (period, day)
        if identity not in periods or identity in seen:
            raise ValueError("Duplicate or unexpected summary period.")
        seen.add(identity)
        start, end = _bounds(day, period, settings["timezone"])
        keys = [(row["SPATIAL_SCOPE"], row["H3_INDEX"], row["METRIC"]) for row in rows]
        if len(keys) != len(expected_keys) or set(keys) != expected_keys:
            raise ValueError("Incomplete or duplicate summary metrics/spatial support.")
        for row in rows:
            expected = int((end - start).total_seconds() / 3600) * (
                len(cells) if row["SPATIAL_SCOPE"] == "region" else 1)
            count = row["VALID_CELL_HOURS"]
            status = "COMPLETE" if count == expected else "PARTIAL" if count else "UNAVAILABLE"
            if (row["PERIOD"] != period or row["LOCAL_START_DATE"] != day.isoformat()
                    or row["TIMEZONE"] != settings["timezone"]
                    or row["PERIOD_START_UTC"] != start or row["PERIOD_END_UTC"] != end
                    or row["UNIT"] != WEATHER_SAMPLE[row["METRIC"]][1]
                    or row["EXPECTED_CELL_HOURS"] != expected or not 0 <= count <= expected
                    or row["COVERAGE_FRACTION"] != count / expected or row["STATUS"] != status):
                raise ValueError("Weather summary coverage/time/unit contract differs.")
            values = [row[name] for name in ("SAMPLED_MIN", "SAMPLED_MEAN", "SAMPLED_MAX")]
            available = row["AVAILABLE_AT_UTC"]
            if count == 0:
                if any(value is not None for value in values) or available is not None:
                    raise ValueError("Unavailable summary must retain null statistics.")
            elif (any(value is None or not np.isfinite(value) for value in values)
                  or not values[0] <= values[1] + 1e-10 <= values[2] + 2e-10
                  or available is None or available < start
                  or (cutoff is not None and available > cutoff)):
                raise ValueError("Weather summary statistics or availability are invalid.")
            if count:
                limit = HARD_LIMITS.get(row["METRIC"])
                if limit and (values[0] < limit.minimum or values[2] > limit.maximum):
                    raise ValueError("Weather summary metric is outside its physical limits.")
        total += len(rows)
    if seen != periods:
        raise ValueError("Weather summary period coverage is incomplete.")
    return total


def validate_weather_summary(manifest_path: str | Path) -> dict:
    path = Path(manifest_path).resolve()
    payload = load_manifest(path)
    if payload["product"] != PRODUCT or payload["method_version"] != method_version(PRODUCT):
        raise ValueError("Incompatible weather summary product/method.")
    settings = payload["resolved_config"]
    cells = settings["h3_cells"]
    if (settings["spatial_weighting"] != "equal_h3_centroid_samples"
            or settings["metrics"] != list(HOURLY_CORE)
            or settings["schema_version"] != "weather-summary-v1"
            or settings["spatial_scope"] not in {"h3", "region", "both"}
            or not cells or len(set(cells)) != len(cells)
            or cell_set_hash(cells) != settings["support_cell_set_hash"]
            or any(not h3.is_valid_cell(cell) or h3.get_resolution(cell) != 5 for cell in cells)):
        raise ValueError("Incompatible summary calculation policy.")
    artifact = resolve_portable_path(payload["artifacts"][0]["path"], base=path.parent)
    count = _validate_table(artifact, settings, payload["temporal_coverage"])
    if count != payload["artifacts"][0]["row_count"]:
        raise ValueError("Summary row count differs from manifest.")
    return dict(valid=True, product=PRODUCT, row_count=count, release_id=payload["release_id"],
                source_evidence_kind=settings["source_evidence_kind"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--spatial-scope", choices=("h3", "region", "both"), default="both")
    parser.add_argument("--as-of-utc")
    args = parser.parse_args()
    print(export_weather_summary(args.manifest, args.output_dir,
                                spatial_scope=args.spatial_scope, as_of_utc=args.as_of_utc))
    return 0
