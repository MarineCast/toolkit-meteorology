"""Artifact, manifest, and atomic-publication helpers for meteorological products."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from meteorology.core.artifacts import atomic_write_json
from meteorology.core.config.paths import project_root

MANIFEST_SCHEMA_VERSION = 2


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksum_path(path: str | Path) -> str:
    target = Path(path)
    if target.is_file():
        return sha256_file(target)
    if not target.is_dir():
        raise FileNotFoundError(target)
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in target.rglob("*") if candidate.is_file()):
        digest.update(str(item.relative_to(target)).encode("utf-8"))
        digest.update(sha256_file(item).encode("ascii"))
    return digest.hexdigest()


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def cell_set_hash(cells: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for cell in sorted({str(value) for value in cells}):
        digest.update(cell.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _source_checkout() -> Path | None:
    package = Path(__file__).resolve().parent
    candidate = package.parent.parent
    return candidate if (candidate / "src/meteorology").resolve() == package and (candidate / ".git").exists() else None


def git_revision() -> str | None:
    root = _source_checkout()
    if root is None:
        return None
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def code_state() -> dict[str, Any]:
    """Hash installed source; never attribute the caller's data repository to this code."""
    package = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".yaml"}:
            digest.update(path.relative_to(package).as_posix().encode())
            digest.update(sha256_file(path).encode("ascii"))
    root = _source_checkout()
    dirty = False
    if root is not None:
        try:
            dirty = bool(subprocess.check_output(
                ["git", "status", "--porcelain", "--", "src/meteorology"],
                cwd=root, text=True, stderr=subprocess.DEVNULL,
            ).strip())
        except (OSError, subprocess.CalledProcessError):
            pass
    return {"revision": git_revision(), "dirty": dirty, "source_hash": digest.hexdigest()}


def parquet_files(path: str | Path) -> list[Path]:
    target = Path(path)
    if target.is_file() and target.suffix.lower() in {".parquet", ".pq"}:
        return [target]
    if not target.is_dir():
        return []
    return sorted(target.rglob("*.parquet"))


def portable_path(path: str | Path) -> str:
    """Return a project-relative path when possible, otherwise an absolute path."""

    resolved = Path(path).resolve()
    roots = []
    candidate_root = os.environ.get("METEOROLOGY_CANDIDATE_ROOT")
    if candidate_root:
        roots.append(Path(candidate_root).resolve())
    roots.append(project_root().resolve())
    for root in roots:
        try:
            return str(resolved.relative_to(root))
        except ValueError:
            continue
    return str(resolved)


def resolve_portable_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    candidate_root = os.environ.get("METEOROLOGY_CANDIDATE_ROOT")
    roots = (
        [Path(candidate_root).resolve(), project_root().resolve()]
        if candidate_root
        else [project_root().resolve()]
    )
    for root in roots:
        resolved = root / candidate
        if resolved.exists():
            return resolved
    return roots[0] / candidate


def parquet_contract(
    path: str | Path,
    *,
    h3_column: str = "H3_INDEX",
    published_path: str | Path | None = None,
) -> dict[str, Any]:
    files = parquet_files(path)
    if not files:
        raise FileNotFoundError(f"No Parquet files found: {path}")
    schema = pq.ParquetFile(files[0]).schema_arrow
    row_count = 0
    cells: set[str] = set()
    for item in files:
        parquet = pq.ParquetFile(item)
        if not parquet.schema_arrow.equals(schema, check_metadata=False):
            raise ValueError(f"Parquet schema drift detected: {item}")
        row_count += int(parquet.metadata.num_rows)
        if h3_column in schema.names:
            cells.update(str(value) for value in parquet.read([h3_column])[h3_column].to_pylist())
    contract = {
        "path": portable_path(published_path or path),
        "checksum": checksum_path(path),
        "file_count": len(files),
        "row_count": row_count,
        "schema": [
            {"name": field.name, "type": str(field.type), "nullable": field.nullable}
            for field in schema
        ],
        "h3_cell_count": len(cells) if cells else None,
        "h3_cell_set_hash": cell_set_hash(cells) if cells else None,
    }
    contract["contract_hash"] = stable_hash(contract)
    return contract


def manifest_payload(
    *,
    product: str,
    run_id: str,
    config_path: Path,
    resolved_config: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
    inputs: Sequence[Mapping[str, Any]] = (),
    sources: Sequence[Mapping[str, Any]] = (),
    h3_resolution: int | None = None,
    spatial_bounds: Mapping[str, float] | None = None,
    temporal_coverage: Mapping[str, Any] | None = None,
    source_completeness: str = "complete",
    availability_semantics: Mapping[str, Any] | str = "not_applicable",
    formulas: Mapping[str, str] | None = None,
    units: Mapping[str, str] | None = None,
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    if not artifacts:
        raise ValueError("A meteorological manifest must contain at least one artifact.")
    live_code = code_state()
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "product": product,
        "run_id": run_id,
        "build_time_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "code_revision": live_code["revision"],
        "code_dirty": live_code["dirty"],
        "code_source_hash": live_code["source_hash"],
        "config_path": portable_path(config_path),
        "resolved_config_hash": stable_hash(resolved_config),
        "resolved_config": dict(resolved_config),
        "inputs": [dict(value) for value in inputs],
        "sources": [dict(value) for value in sources],
        "artifacts": [dict(value) for value in artifacts],
        "h3_resolution": h3_resolution,
        "spatial_bounds_wgs84": dict(spatial_bounds or {}),
        "temporal_coverage": dict(temporal_coverage or {}),
        "source_completeness": source_completeness,
        "availability_semantics": (
            dict(availability_semantics)
            if isinstance(availability_semantics, Mapping)
            else str(availability_semantics)
        ),
        "formulas": dict(formulas or {}),
        "units": dict(units or {}),
        "known_limitations": list(limitations),
    }


def write_manifest(path: str | Path, payload: Mapping[str, Any]) -> Path:
    validate_manifest(payload, verify_artifacts=False)
    return atomic_write_json(path, dict(payload), overwrite=True)


def load_manifest(path: str | Path, *, verify_artifacts: bool = True) -> dict[str, Any]:
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Meteorological manifest root must be a mapping: {manifest_path}")
    validate_manifest(payload, verify_artifacts=verify_artifacts)
    return payload


def validate_manifest(payload: Mapping[str, Any], *, verify_artifacts: bool = True) -> None:
    required = {
        "manifest_schema_version",
        "product",
        "run_id",
        "build_time_utc",
        "code_revision",
        "code_dirty",
        "code_source_hash",
        "config_path",
        "resolved_config_hash",
        "resolved_config",
        "sources",
        "inputs",
        "artifacts",
        "h3_resolution",
        "spatial_bounds_wgs84",
        "temporal_coverage",
        "source_completeness",
        "availability_semantics",
        "formulas",
        "units",
        "known_limitations",
    }
    missing = sorted(required.difference(payload))
    if missing:
        raise ValueError(f"Meteorological manifest is missing fields: {missing}")
    if int(payload["manifest_schema_version"]) != MANIFEST_SCHEMA_VERSION:
        raise ValueError("Unsupported meteorological manifest schema version.")
    if payload["resolved_config_hash"] != stable_hash(payload["resolved_config"]):
        raise ValueError("Meteorological manifest resolved-config hash is invalid.")
    if not isinstance(payload["code_dirty"], bool):
        raise ValueError("Meteorological manifest code_dirty must be boolean.")
    if not isinstance(payload["code_source_hash"], str) or not payload["code_source_hash"]:
        raise ValueError("Meteorological manifest code_source_hash is invalid.")
    artifacts = payload["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Meteorological manifest artifacts must be a non-empty list.")
    production = str(payload["product"]).startswith("meteorological.")
    if production:
        sources = payload["sources"]
        if not isinstance(sources, list) or not sources:
            raise ValueError("Meteorological production manifests require source metadata.")
        source_fields = {
            "name",
            "license",
            "attribution",
            "observation_period",
            "redistribution_restrictions",
        }
        for source in sources:
            if not isinstance(source, Mapping) or source_fields.difference(source):
                raise ValueError(
                    "Meteorological source metadata requires name, license, attribution, "
                    "observation_period, and redistribution_restrictions."
                )
        for source_input in payload["inputs"]:
            if (
                not isinstance(source_input, Mapping)
                or not source_input.get("path")
                or not source_input.get("checksum")
            ):
                raise ValueError("Meteorological manifest inputs require path and checksum.")
        artifact_fields = {
            "path",
            "checksum",
            "file_count",
            "row_count",
            "schema",
            "h3_cell_count",
            "h3_cell_set_hash",
            "contract_hash",
        }
        for artifact in artifacts:
            if not isinstance(artifact, Mapping) or artifact_fields.difference(artifact):
                raise ValueError(
                    "Meteorological artifact contracts require path, checksum, file/row counts, "
                    "Arrow schema, and H3 cell-set fields."
                )
            if not isinstance(artifact["schema"], list) or not artifact["schema"]:
                raise ValueError("Meteorological artifact Arrow schemas must be non-empty.")
        if (
            payload["product"] != "meteorological.spatial_support"
            and not payload["temporal_coverage"]
        ):
            raise ValueError("Dated meteorological products require temporal coverage.")
    if not verify_artifacts:
        return
    for source_input in payload["inputs"]:
        path = resolve_portable_path(str(source_input["path"]))
        if not path.exists():
            raise FileNotFoundError(f"Manifest input does not exist: {path}")
        observed = checksum_path(path)
        if observed != source_input["checksum"]:
            raise ValueError(
                f"Manifest input checksum mismatch for {path}: "
                f"expected {source_input['checksum']}, observed {observed}."
            )
    for artifact in artifacts:
        if (
            not isinstance(artifact, Mapping)
            or not artifact.get("path")
            or not artifact.get("checksum")
        ):
            raise ValueError("Each meteorological artifact requires path and checksum.")
        path = resolve_portable_path(str(artifact["path"]))
        if not path.exists():
            raise FileNotFoundError(f"Manifest artifact does not exist: {path}")
        observed = checksum_path(path)
        if observed != artifact["checksum"]:
            raise ValueError(
                f"Manifest checksum mismatch for {path}: expected {artifact['checksum']}, "
                f"observed {observed}."
            )
        if production:
            contract_payload = {
                key: value for key, value in artifact.items() if key != "contract_hash"
            }
            if artifact["contract_hash"] != stable_hash(contract_payload):
                raise ValueError(f"Manifest artifact contract hash mismatch for {path}.")


def write_table(path: str | Path, table: pa.Table, schema: pa.Schema) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    normalized = table.cast(schema, safe=False)
    pq.write_table(normalized, destination, compression="zstd")
    return destination


def write_year_partitions(
    table: pa.Table,
    root: str | Path,
    schema: pa.Schema,
    *,
    date_column: str = "DATE",
) -> Path:
    destination = Path(root)
    destination.mkdir(parents=True, exist_ok=True)
    frame = table.cast(schema, safe=False).to_pandas()
    if date_column not in frame.columns:
        raise ValueError(f"Partition table is missing {date_column}.")
    years = frame[date_column].astype(str).str[:4]
    for year, group in frame.groupby(years, sort=True):
        file_path = destination / f"year={year}" / "part-000.parquet"
        write_table(file_path, pa.Table.from_pandas(group, preserve_index=False), schema)
    return destination
