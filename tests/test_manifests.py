from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest

from meteorology.core.artifacts import TransactionalFamilyPublisher
from meteorology.artifacts import (
    checksum_path,
    load_manifest,
    manifest_payload,
    parquet_contract,
    portable_path,
    resolve_portable_path,
    write_manifest,
    write_table,
)
from meteorology.migration import (
    _recover_interrupted_migrations,
)


def test_manifest_detects_artifact_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("original")
    manifest = tmp_path / "MANIFEST.json"
    payload = manifest_payload(
        product="fixture",
        run_id="fixture",
        config_path=tmp_path / "config.yaml",
        resolved_config={"value": 1},
        artifacts=[{"path": str(artifact), "checksum": checksum_path(artifact)}],
    )
    write_manifest(manifest, payload)
    load_manifest(manifest, verify_artifacts=True)
    artifact.write_text("tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_manifest(manifest, verify_artifacts=True)


def test_manifest_paths_are_project_relative_for_canonical_artifacts() -> None:
    path = Path("data/processed/domain/environmental_layer/meteorological/fixture.parquet")
    assert portable_path(path) == str(path)


def test_manifest_paths_are_candidate_relative_and_resolve_in_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate"
    artifact = candidate / "data/processed/fixture.parquet"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("fixture")
    monkeypatch.setenv("METEOROLOGY_CANDIDATE_ROOT", str(candidate))

    assert portable_path(artifact) == "data/processed/fixture.parquet"
    assert resolve_portable_path("data/processed/fixture.parquet") == artifact


def test_manifest_detects_schema_contract_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.parquet"
    schema = pa.schema(
        [
            pa.field("H3_INDEX", pa.string(), nullable=False),
            pa.field("VALUE", pa.float64(), nullable=False),
        ]
    )
    write_table(
        artifact,
        pa.Table.from_pylist([{"H3_INDEX": "cell", "VALUE": 1.0}], schema=schema),
        schema,
    )
    contract = parquet_contract(artifact)
    contract["row_count"] = 2
    manifest = tmp_path / "MANIFEST.json"
    payload = manifest_payload(
        product="meteorological.fixture",
        run_id="fixture",
        config_path=tmp_path / "config.yaml",
        resolved_config={"value": 1},
        artifacts=[contract],
        sources=[
            {
                "name": "fixture",
                "license": "fixture",
                "attribution": "fixture",
                "observation_period": "fixture",
                "redistribution_restrictions": "none",
            }
        ],
        temporal_coverage={"start_date": "2024-01-01", "end_date": "2024-01-01"},
    )
    write_manifest(manifest, payload)
    with pytest.raises(ValueError, match="contract hash mismatch"):
        load_manifest(manifest, verify_artifacts=True)


def test_atomic_product_publisher_restores_existing_family_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("old-first")
    second.write_text("old-second")
    original_replace = os.replace
    calls = 0

    def fail_fourth_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("fixture promotion failure")
        return original_replace(source, destination)

    with pytest.raises(OSError, match="fixture promotion failure"):
        with TransactionalFamilyPublisher(tmp_path, run_id="rollback-fixture") as publisher:
            staged_first = publisher.stage_path(first)
            staged_second = publisher.stage_path(second)
            staged_first.write_text("new-first")
            staged_second.write_text("new-second")
            monkeypatch.setattr(os, "replace", fail_fourth_replace)
            publisher.publish()
    assert first.read_text() == "old-first"
    assert second.read_text() == "old-second"


def test_atomic_product_publisher_recovers_interrupted_transaction(tmp_path: Path) -> None:
    destination = tmp_path / "product.txt"
    destination.write_text("partially-promoted")
    transaction = tmp_path / ".transactions" / "interrupted"
    backup = transaction / "backups" / "000_product.txt"
    backup.parent.mkdir(parents=True)
    backup.write_text("canonical-before-crash")
    (transaction / "journal.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "interrupted",
                "phase": "promoting",
                "items": [
                    {
                        "destination": str(destination),
                        "candidate": str(tmp_path / ".staging/interrupted/000_product.txt"),
                        "backup": str(backup),
                        "had_destination": True,
                        "backed_up": True,
                        "promoted": True,
                    }
                ],
            }
        )
    )
    with TransactionalFamilyPublisher(tmp_path, run_id="next-run"):
        pass
    assert destination.read_text() == "canonical-before-crash"
    assert not transaction.exists()


def test_manifest_detects_input_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    artifact = tmp_path / "artifact.txt"
    source.write_text("source")
    artifact.write_text("artifact")
    manifest = tmp_path / "MANIFEST.json"
    payload = manifest_payload(
        product="fixture",
        run_id="fixture-input",
        config_path=tmp_path / "config.yaml",
        resolved_config={},
        inputs=[{"path": str(source), "checksum": checksum_path(source)}],
        artifacts=[{"path": str(artifact), "checksum": checksum_path(artifact)}],
    )
    write_manifest(manifest, payload)
    source.write_text("tampered")
    with pytest.raises(ValueError, match="input checksum mismatch"):
        load_manifest(manifest, verify_artifacts=True)


def test_legacy_migration_journal_rolls_back_interrupted_moves(tmp_path: Path) -> None:
    source = tmp_path / "data/legacy/product.txt"
    archive = tmp_path / "migrations/run"
    destination = archive / "data/legacy/product.txt"
    destination.parent.mkdir(parents=True)
    destination.write_text("legacy")
    (archive / "MIGRATION_JOURNAL.json").write_text(
        json.dumps(
            {
                "state": "MOVING",
                "artifacts": [
                    {"source": str(source), "destination": str(destination), "moved": True}
                ],
            }
        )
    )
    _recover_interrupted_migrations(tmp_path / "migrations")
    assert source.read_text() == "legacy"
    assert not archive.exists()


def test_complete_legacy_migration_journal_is_finalized(tmp_path: Path) -> None:
    archive = tmp_path / "migrations/run"
    archive.mkdir(parents=True)
    journal = archive / "MIGRATION_JOURNAL.json"
    journal.write_text(json.dumps({"state": "COMPLETE", "artifacts": []}))
    _recover_interrupted_migrations(tmp_path / "migrations")
    assert not journal.exists()
    assert json.loads((archive / "MIGRATION_MANIFEST.json").read_text())["state"] == "COMPLETE"
