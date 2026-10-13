"""Reproducible, resumable one-week live HRRR documentation example."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import platform
from importlib.metadata import PackageNotFoundError, version
import resource
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pyarrow.parquet as pq

from .artifacts import checksum_path
from .cli import initialize_workspace
from .config import load_meteorological_config
from .core.artifacts import atomic_write_json
from .hourly_weather.live import TransferBudget, fetch_hour
from .hourly_weather.product import acquire_hourly_weather, build_hourly_weather
from .releases import freeze_release
from .spatial_support.build import build_meteorological_spatial_support, load_meteorological_support
from .temporal_products import hourly_utc_instants
from .validation import validate_product
from .weather_summary import export_weather_summary


def _versions() -> dict:
    result = {}
    for name in ("numpy", "pandas", "pyarrow", "cfgrib", "eccodes", "xarray"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = "not installed"
    return result


def tree_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def run(output: Path, *, start_date: str = "2024-01-01", max_requests: int = 1600,
        max_bytes: int = 4 * 1024**3, dry_run: bool = False) -> dict:
    first = date.fromisoformat(start_date)
    if first.weekday() != 0:
        raise ValueError("Use a Monday start for a complete calendar-week demonstration.")
    if max_requests <= 0 or max_bytes <= 0:
        raise ValueError("Request and byte limits must be positive.")
    days = [(first + timedelta(days=i)).isoformat() for i in range(7)]
    plan = dict(start_date=days[0], end_date=days[-1], timezone="America/Los_Angeles",
                max_requests=max_requests, max_bytes=max_bytes, schema="live-week-demo-v1",
                source="NOAA HRRR AWS sfc/f00", spatial_scope="both")
    if dry_run:
        hours = sum(len(hourly_utc_instants(day, plan["timezone"])) for day in days)
        return dict(**plan, source_cycles=hours, planned_http_requests=hours * 9,
                    payload_bytes="unknown until source indexes are read; hard cap applies",
                    network_requests_issued=0)
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".demo.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another writer owns this demo output.") from exc
        request = output / "REQUEST.json"
        if request.exists():
            if json.loads(request.read_text()) != plan:
                raise ValueError("Resume requires the same dates, limits and demo contract.")
        else:
            if any(p.name != ".demo.lock" for p in output.iterdir()):
                raise ValueError("Choose a fresh output directory for a new demo.")
            atomic_write_json(request, plan)
        old = os.environ.get("METEOROLOGY_WORKSPACE")
        workspace = output / "workspace"
        started = time.perf_counter()
        budget = TransferBudget(output / "TRANSFER.json", max_requests=max_requests, max_bytes=max_bytes)
        report_path = output / "REPORT.json"
        previous = json.loads(report_path.read_text()) if report_path.exists() else {}
        initial_requests = budget.state["requests"]
        initial_bytes = budget.state["received_bytes"]
        report = dict(**plan, status="RUNNING", daily=[],
                      empirical_scientific_acceptance="NOT_RUN",
                      forecast_skill="NOT_RUN", observational_comparison="NOT_RUN")
        os.environ["METEOROLOGY_WORKSPACE"] = str(workspace)
        try:
            initialize_workspace(workspace)
            config_path = workspace / "config/data/environment_meteorological.yaml"
            config_hashes = {str(p.relative_to(workspace)): checksum_path(p)
                             for p in (config_path, workspace / "config/common.yaml")}
            pinned = output / "CONFIG_HASHES.json"
            if pinned.exists() and json.loads(pinned.read_text()) != config_hashes:
                raise ValueError("Demo configuration changed; use a new run directory.")
            atomic_write_json(pinned, config_hashes, overwrite=True)
            config = load_meteorological_config(config_path)
            build_meteorological_spatial_support(config_path)
            support = load_meteorological_support(5, config_path)
            report.update(h3_cells=len(support), bbox=config.bbox)
            decoded = workspace / "retained-decoded"
            manifests = []
            for day in days:
                day_root = output / "releases" / day
                existing = list(day_root.glob("*/MANIFEST.json"))
                if len(existing) > 1:
                    raise ValueError("Multiple releases for a demo day.")
                if existing:
                    frozen = existing[0]
                    gate = validate_product(frozen)
                    if gate.get("source_evidence_kind") != "retained_decoded_hrrr":
                        raise ValueError("Live demo requires actual retained HRRR evidence.")
                else:
                    for valid in hourly_utc_instants(day, config.surface_weather.timezone):
                        cached = fetch_hour(valid, config=config, decoded=decoded, budget=budget)
                        print(f"{valid.isoformat()} {'cached' if cached else 'acquired'}; "
                              f"{budget.state['received_bytes'] / 1024**2:.1f} MiB payload", file=sys.stderr, flush=True)
                    acquired = acquire_hourly_weather(config_path, local_date=day, decoded_dir=decoded)
                    validate_product(acquired["manifest"])
                    product = build_hourly_weather(config_path)
                    validate_product(product)
                    frozen = freeze_release(product, day_root)
                    gate = validate_product(frozen)
                    if gate.get("source_evidence_kind") != "retained_decoded_hrrr":
                        raise ValueError("Live demo requires actual retained HRRR evidence.")
                payload = json.loads(frozen.read_text())
                if (payload["resolved_config"]["local_date"] != day
                        or payload["resolved_config"]["timezone"] != plan["timezone"]
                        or payload["spatial_bounds_wgs84"] != config.bbox):
                    raise ValueError("Frozen daily release differs from the pinned demo request.")
                manifests.append(frozen)
                report["daily"].append(dict(date=day, manifest=str(frozen.relative_to(output)),
                    manifest_sha256=checksum_path(frozen), validation="PASS"))
                atomic_write_json(report_path, report, overwrite=True)
                print(f"{day}: frozen release validated", file=sys.stderr, flush=True)
            summaries = {}
            for scope in ("both", "region"):
                directory = output / f"summary-{scope}"
                manifest = directory / "MANIFEST.json"
                if not manifest.exists():
                    manifest = export_weather_summary(manifests, directory, spatial_scope=scope)
                validate_product(manifest)
                summary = json.loads(manifest.read_text())
                consumed = [item["checksum"] for item in summary["inputs"]
                            if item["role"] == "hourly_manifest"]
                if (consumed != [checksum_path(path) for path in manifests]
                        or summary["resolved_config"]["spatial_scope"] != scope
                        or summary["resolved_config"]["as_of_utc"] is not None):
                    raise ValueError("Summary does not reference this demo's exact daily releases.")
                artifact = directory / "weather-summary.parquet"
                summaries[scope] = dict(manifest=str(manifest.relative_to(output)),
                    parquet_bytes=artifact.stat().st_size, rows=pq.ParquetFile(artifact).metadata.num_rows,
                    sha256=checksum_path(artifact))
            report.update(status="COMPLETE", real_source_compatibility="PASS for these cycles and bounds",
                          summaries=summaries)
        except BaseException as exc:
            report.update(status="INCOMPLETE", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            elapsed = time.perf_counter() - started
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak_mib = peak / (1024**2 if sys.platform == "darwin" else 1024)
            invocations = previous.get("invocations", [])
            invocations.append(dict(status=report["status"], elapsed_seconds=elapsed,
                peak_rss_mib=peak_mib, requests=budget.state["requests"] - initial_requests,
                received_bytes=budget.state["received_bytes"] - initial_bytes))
            report.update(invocations=invocations, python_version=platform.python_version(),
                          dependency_versions=_versions(),
                          peak_rss_mib_all_invocations=max(peak_mib, previous.get("peak_rss_mib_all_invocations", previous.get("peak_rss_mib_this_process", 0))),
                          elapsed_seconds_this_invocation=elapsed,
                          elapsed_seconds_all_invocations=previous.get("elapsed_seconds_all_invocations", 0) + elapsed,
                          peak_rss_mib_this_process=peak_mib,
                          transfer_requests=budget.state["requests"],
                          reserved_payload_bytes=budget.state["reserved_bytes"],
                          received_payload_bytes=budget.state["received_bytes"],
                          storage_bytes={"workspace_including_evidence": tree_bytes(workspace),
                                         "frozen_releases_including_evidence": tree_bytes(output / "releases"),
                                         "summary_both": tree_bytes(output / "summary-both"),
                                         "summary_region": tree_bytes(output / "summary-region")})
            try:
                atomic_write_json(report_path, report, overwrite=True)
            finally:
                if old is None:
                    os.environ.pop("METEOROLOGY_WORKSPACE", None)
                else:
                    os.environ["METEOROLOGY_WORKSPACE"] = old
        from .week_demo_report import write_demo_report
        write_demo_report(output)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--max-requests", type=int, default=1600)
    parser.add_argument("--max-bytes", type=int, default=4 * 1024**3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(**vars(args)), indent=2))


if __name__ == "__main__":
    main()
