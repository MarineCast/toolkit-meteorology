"""Bounded HTTP acquisition of the eight f00 atmosphere messages from NOAA AWS."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pandas as pd

from ..artifacts import checksum_path
from ..core.artifacts import atomic_write_json
from ..surface_weather.source import (
    HRRR_VARIABLES, decode_hrrr_fields, hrrr_aws_archive_uri, normalize_flat_grid,
)
from .product import _bundle_paths, _load_decoded_grid, retain_decoded_hourly_grid

FIELDS = {k: v for k, v in HRRR_VARIABLES.items() if k != "precip_rate_kg_m2_s"}
INDEX_LIMIT = 65536


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Archive redirects are not allowed by the request budget.")


class TransferBudget:
    """Persistent conservative reservations; caller must hold the run's writer lock."""

    def __init__(self, path: Path, *, max_requests: int, max_bytes: int):
        if max_requests <= 0 or max_bytes <= 0:
            raise ValueError("Request and byte limits must be positive.")
        self.path = path
        self.state = (json.loads(path.read_text()) if path.exists() else dict(
            max_requests=max_requests, max_bytes=max_bytes, requests=0,
            reserved_bytes=0, received_bytes=0, transfers=[]))
        if (self.state["max_requests"], self.state["max_bytes"]) != (max_requests, max_bytes):
            raise ValueError("Resume requires the original transfer limits.")
        self.opener = build_opener(_NoRedirect())

    def save(self):
        atomic_write_json(self.path, self.state, overwrite=True)
        with self.path.open("rb") as handle:
            os.fsync(handle.fileno())
        parent = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    def get(self, url: str, destination: Path, *, limit: int,
            byte_range: tuple[int, int] | None = None) -> None:
        if limit <= 0 or (byte_range and (
                byte_range[0] < 0 or byte_range[1] - byte_range[0] + 1 != limit)):
            raise ValueError("A positive exact response payload reservation is required.")
        if (self.state["requests"] + 1 > self.state["max_requests"]
                or self.state["reserved_bytes"] + limit > self.state["max_bytes"]):
            raise ValueError("Persistent transfer budget exhausted; no request issued.")
        record = dict(url=url, byte_range=byte_range, reserved_bytes=limit,
                      received_bytes=0, status="reserved")
        self.state["requests"] += 1
        self.state["reserved_bytes"] += limit
        self.state["transfers"].append(record)
        self.save()  # Count an interrupted request conservatively before issuing it.
        headers = {"Accept-Encoding": "identity", "User-Agent": "toolkit-meteorology-week-demo"}
        if byte_range:
            headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        try:
            with self.opener.open(Request(url, headers=headers), timeout=60) as response:
                if byte_range:
                    expected = f"bytes {byte_range[0]}-{byte_range[1]}/"
                    if response.status != 206 or not response.headers.get("Content-Range", "").startswith(expected):
                        raise ValueError("Archive did not honor the exact requested byte range.")
                elif response.status != 200:
                    raise ValueError("Unexpected archive index response.")
                size = int(response.headers.get("Content-Length", "-1"))
                if size < 0 or size > limit or (byte_range and size != limit):
                    raise ValueError("Archive response length exceeds or differs from its budget.")
                with destination.open("wb") as handle:
                    remaining = size
                    while remaining:
                        chunk = response.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError("Truncated archive response.")
                        handle.write(chunk)
                        remaining -= len(chunk)
                        record["received_bytes"] += len(chunk)
                        self.state["received_bytes"] += len(chunk)
            record["status"] = "complete"
        except BaseException:
            record["status"] = "failed_or_interrupted"
            raise
        finally:
            self.save()


def selected_ranges(index: str) -> list[tuple[int, int]]:
    lines = [line for line in index.splitlines() if line.strip()]
    offsets = [int(line.split(":", 2)[1]) for line in lines]
    if not offsets or offsets[0] < 0 or any(b <= a for a, b in zip(offsets, offsets[1:])):
        raise ValueError("Archive inventory has invalid byte offsets.")
    selected = []
    for selector in FIELDS.values():
        matches = [i for i, line in enumerate(lines) if selector in line]
        if len(matches) != 1 or matches[0] + 1 >= len(lines):
            raise ValueError(f"Missing, ambiguous or unbounded inventory field: {selector}")
        i = matches[0]
        selected.append((offsets[i], offsets[i + 1] - 1))
    return sorted(selected)


def fetch_hour(valid: pd.Timestamp, *, config, decoded: Path, budget: TransferBudget) -> bool:
    """Return True for a validated cached bundle; never replace a committed hour."""
    parquet, evidence = _bundle_paths(decoded, valid)
    receipt_path = parquet.parent / "transfer.json"
    if not receipt_path.exists():
        receipt_path = decoded / f"{valid:%Y%m%dT%HZ}.receipt.json"
    if parquet.exists() or evidence.exists():
        _load_decoded_grid(parquet, evidence, valid=valid,
                           lag_hours=config.surface_weather.availability_lag_hours)
        receipt = json.loads(receipt_path.read_text())
        if (receipt["grid_sha256"] != checksum_path(parquet)
                or receipt["evidence_sha256"] != checksum_path(evidence)):
            raise ValueError("Cached live bundle differs from its acquisition receipt.")
        return True
    import cfgrib

    decoded.mkdir(parents=True, exist_ok=True)
    scratch = decoded / f".{valid:%Y%m%dT%HZ}.transfer"
    scratch.mkdir(exist_ok=True)
    uri = hrrr_aws_archive_uri(valid)
    index = scratch / "source.idx"
    budget.get(uri + ".idx", index, limit=INDEX_LIMIT)
    ranges = selected_ranges(index.read_text())
    # Refuse the cycle before any field transfer if its complete payload cannot fit.
    if (budget.state["reserved_bytes"] + sum(b - a + 1 for a, b in ranges) > budget.state["max_bytes"]
            or budget.state["requests"] + len(ranges) > budget.state["max_requests"]):
        raise ValueError("Remaining budget cannot cover this complete source cycle.")
    grib = scratch / "selected.grib2"
    with grib.open("wb") as target:
        for start, end in ranges:
            part = scratch / "part.grib2"
            budget.get(uri, part, limit=end-start+1, byte_range=(start, end))
            with part.open("rb") as source:
                while chunk := source.read(65536):
                    target.write(chunk)
            part.unlink()
    parts = cfgrib.open_datasets(str(grib), backend_kwargs=dict(
        indexpath="", errors="raise", read_keys=["parameterName", "parameterUnits",
        "stepRange", "uvRelativeToGrid"]))
    try:
        flat, units = decode_hrrr_fields(
            parts, valid_time_utc=valid, bbox=config.bbox,
            bbox_padding_degrees=config.surface_weather.bbox_padding_degrees,
            variables=FIELDS)
        grid = normalize_flat_grid(flat, units_by_variable=units, valid_time_utc=valid,
                                   availability_lag_hours=config.surface_weather.availability_lag_hours,
                                   include_precipitation=False)
        parquet, evidence = retain_decoded_hourly_grid(
            grid, valid_time_utc=valid.isoformat(), source_uri=uri,
            retrieved_at_utc=datetime.now(timezone.utc).isoformat(), output_dir=decoded,
            source_receipt=dict(source_uri=uri, ranges=ranges,
                selected_grib_sha256=checksum_path(grib), index_sha256=checksum_path(index)))
    finally:
        for dataset in parts:
            dataset.close()
    grib.unlink()
    index.unlink()
    scratch.rmdir()
    return False
