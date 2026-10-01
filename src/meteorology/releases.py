"""Freeze a validated family into a relocatable, checksum-backed data-release directory."""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from .artifacts import checksum_path, resolve_portable_path, stable_hash, write_manifest
from .validation import validate_product


def _copy_verified(source: Path, destination: Path, checksum: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)
    if checksum_path(destination) != checksum:
        raise ValueError(f"Frozen copy checksum mismatch: {destination}")


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe HRRR inventory relative path: {value!r}")
    return path


def freeze_release(manifest_path: str | Path, output_root: str | Path) -> Path:
    """Copy a validated release and declared inputs without modifying its source.

    For surface weather, also copy every sample and crosswalk addressed by the
    canonical acquisition inventory. This is an explicit potentially large copy.
    """

    validate_product(manifest_path)
    source_manifest = Path(manifest_path).resolve()
    payload: dict[str, Any] = json.loads(source_manifest.read_text(encoding="utf-8"))
    release_id = str(payload["release_id"])
    root = Path(output_root).resolve()
    destination = root / release_id
    if destination.exists():
        raise FileExistsError(f"Data release already exists: {destination}")
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".staging-{release_id[:12]}-{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    try:
        for index, item in enumerate(payload["artifacts"]):
            source = resolve_portable_path(item["path"], base=source_manifest.parent)
            target = staging / "artifacts" / f"{index:02d}_{source.name}"
            _copy_verified(source, target, item["checksum"])
            item["path"] = target.relative_to(staging).as_posix()
            item["contract_hash"] = stable_hash({key: value for key, value in item.items() if key != "contract_hash"})
        copied: dict[Path, Path] = {}
        inventory_source: Path | None = None
        for index, item in enumerate(payload["inputs"]):
            source = resolve_portable_path(item["path"], base=source_manifest.parent)
            if source in copied:
                target = copied[source]
            else:
                target = (staging / "inputs" / "hrrr_samples" / source.name) if (
                    payload["product"] == "meteorological.surface_weather"
                    and source.name.endswith("SOURCE_INVENTORY.parquet")
                ) else staging / "inputs" / f"{index:02d}_{source.name}"
                _copy_verified(source, target, item["checksum"])
                copied[source] = target
            item["path"] = target.relative_to(staging).as_posix()
            if payload["product"] == "meteorological.surface_weather" and source.name.endswith("SOURCE_INVENTORY.parquet"):
                inventory_source = source
        if inventory_source is not None:
            inventory = pq.read_table(inventory_source).to_pandas()
            raw_root = inventory_source.parent
            count = 0
            for row in inventory.itertuples(index=False):
                for relative_field, checksum_field in (("RELATIVE_PATH", "CHECKSUM"), ("CROSSWALK_RELATIVE_PATH", "CROSSWALK_CHECKSUM")):
                    relative = _safe_relative(str(getattr(row, relative_field)))
                    checksum = str(getattr(row, checksum_field))
                    target = staging / "inputs" / "hrrr_samples" / relative
                    if target.exists():
                        if checksum_path(target) != checksum:
                            raise ValueError(f"Conflicting HRRR sample identity: {relative}")
                        continue
                    _copy_verified(raw_root / relative, target, checksum)
                    count += 1
            payload["archived_hrrr_samples"] = {
                "path": "inputs/hrrr_samples",
                "checksum": checksum_path(staging / "inputs" / "hrrr_samples"),
                "file_count": count,
            }
        write_manifest(staging / "MANIFEST.json", payload)
        if destination.exists():
            raise FileExistsError(f"Data release already exists: {destination}")
        staging.rename(destination)
        # Files stay writable to permit explicit owner cleanup; the release API
        # refuses replacement and its checksums detect mutation.
        return destination / "MANIFEST.json"
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(freeze_release(args.manifest, args.output_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
