"""Plan or execute the recoverable migration of legacy meteorological artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from meteorology.core.artifacts import atomic_write_json
from meteorology.core.config.paths import project_root

from .artifacts import checksum_path, load_manifest
from .config import DEFAULT_CONFIG_PATH, load_meteorological_config
from .surface_weather.verify import verification_report_path

LEGACY_RELATIVE_PATHS = (
    "data/processed/domain/environmental_layer/meteorological/surface_weather/hrrr",
    "data/processed/domain/environmental_layer/meteorological/surface_weather/H3_SURFACE_WEATHER_SUBDAILY_RES_6",
    "data/processed/domain/environmental_layer/meteorological/surface_weather/H3_SURFACE_WEATHER_DAILY_RES_6",
    "data/processed/domain/environmental_layer/meteorological/surface_weather/H3_HRRR_NEAREST_GRID_RES_6",
    "data/processed/domain/environmental_layer/meteorological/surface_weather/PRE_R5_SURFACE_WEATHER_MANIFEST.json",
    "data/processed/domain/environmental_layer/meteorological/visibility",
    "data/raw/environment/meteorological/surface_weather/hrrr/grids",
    "data/raw/environment/meteorological/surface_weather/hrrr/HRRR_SOURCE_INVENTORY.parquet",
    "data/raw/environment/meteorological/surface_weather/hrrr/DOWNLOAD_MANIFEST.json",
    "data/processed/domain/environmental_layer/meteorological/daylight/H3_DAYLIGHT_FEATURES.parquet",
    "data/processed/domain/environmental_layer/meteorological/daylight/H3_DAYLIGHT_FEATURES.parquet.manifest.json",
    "data/processed/domain/environmental_layer/meteorological/lunar/LUNAR_ILLUMINATION_FEATURES",
    "data/processed/domain/environmental_layer/meteorological/lunar/LUNAR_ILLUMINATION_FEATURES.parquet",
    "data/processed/domain/environmental_layer/meteorological/lunar/LUNAR_ILLUMINATION_FEATURES.parquet.manifest.json",
)


def _recover_interrupted_migrations(migration_root: Path) -> None:
    if not migration_root.exists():
        return
    for journal in sorted(migration_root.glob("*/MIGRATION_JOURNAL.json")):
        payload = json.loads(journal.read_text(encoding="utf-8"))
        if payload.get("state") == "COMPLETE":
            manifest = journal.with_name("MIGRATION_MANIFEST.json")
            if not manifest.exists():
                atomic_write_json(manifest, payload, overwrite=False)
            journal.unlink()
            continue
        for item in reversed(payload.get("artifacts", [])):
            source = Path(str(item["source"]))
            destination = Path(str(item["destination"]))
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, source)
        shutil.rmtree(journal.parent, ignore_errors=True)


def migrate_legacy_meteorological_artifacts(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    archive_root: str | Path | None = None,
    execute: bool = False,
) -> dict[str, object]:
    config = load_meteorological_config(config_path)
    replacement_manifests = (
        config.surface_weather.manifest_path,
        config.daylight.manifest_path,
        config.lunar.manifest_path,
    )
    missing = [path for path in replacement_manifests if not path.exists()]
    if missing:
        formatted = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(
            "Legacy meteorological migration is blocked until every replacement manifest "
            f"exists and validates. Missing: {formatted}"
        )
    for manifest in replacement_manifests:
        load_manifest(manifest, verify_artifacts=True)
    weather_manifest = load_manifest(config.surface_weather.manifest_path, verify_artifacts=True)
    resolved = weather_manifest.get("resolved_config", {})
    if (
        weather_manifest.get("product") != "meteorological.surface_weather"
        or int(resolved.get("h3_resolution", -1)) != 5
    ):
        raise ValueError("Legacy migration requires a verified canonical R5 weather manifest.")
    verification_path = verification_report_path(config_path)
    if not verification_path.exists():
        raise FileNotFoundError(
            "Legacy migration requires the direct-HRRR R5 rebuild verification report: "
            f"{verification_path}"
        )
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    if not verification.get("passed"):
        raise ValueError("Legacy migration requires a passing direct-HRRR R5 verification report.")
    if verification.get("weather_manifest_checksum") != checksum_path(
        config.surface_weather.manifest_path
    ):
        raise ValueError("The R5 verification report does not match the current weather manifest.")
    root = project_root()
    migration_root = root / "data/migrations/meteorological_legacy"
    if execute:
        _recover_interrupted_migrations(migration_root)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = (
        Path(archive_root).expanduser().resolve()
        if archive_root
        else root / "data/migrations/meteorological_legacy" / stamp
    )
    existing = [root / relative for relative in LEGACY_RELATIVE_PATHS if (root / relative).exists()]
    plan = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "execute": execute,
        "archive_root": str(archive),
        "artifacts": [{"source": str(path), "checksum": checksum_path(path)} for path in existing],
    }
    if not execute:
        return plan
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.parent.stat().st_dev != root.stat().st_dev:
        raise ValueError("Recoverable legacy migration requires an archive on the same filesystem.")
    archive.mkdir(parents=True, exist_ok=False)
    journal = archive / "MIGRATION_JOURNAL.json"
    plan["artifacts"] = [
        {
            "source": str(path),
            "destination": str(archive / path.relative_to(root)),
            "checksum": checksum_path(path),
            "moved": False,
        }
        for path in existing
    ]
    atomic_write_json(journal, plan, overwrite=False)
    for item in plan["artifacts"]:
        path = Path(str(item["source"]))
        relative = path.relative_to(root)
        destination = archive / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, destination)
        item["moved"] = True
        atomic_write_json(journal, plan, overwrite=True)
    for item in plan["artifacts"]:
        destination = Path(str(item["destination"]))
        if checksum_path(destination) != str(item["checksum"]):
            raise ValueError(f"Archived legacy artifact checksum mismatch: {destination}")
    plan["state"] = "COMPLETE"
    atomic_write_json(journal, plan, overwrite=True)
    manifest = archive / "MIGRATION_MANIFEST.json"
    atomic_write_json(manifest, plan, overwrite=False)
    journal.unlink()
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--archive-root")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            migrate_legacy_meteorological_artifacts(
                args.config, archive_root=args.archive_root, execute=args.execute
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
