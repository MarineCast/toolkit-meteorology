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
    _migration_lock,
    _recover_interrupted_migrations,
)
from meteorology.validation import validate_product
from meteorology.core.data.meteorological_schemas import SUPPORT_SCHEMA


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


def test_historical_method_remains_checksum_readable_without_current_deep_validation(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "support.parquet"
    write_table(
        artifact,
        pa.Table.from_pylist([{
            "H3_INDEX": "cell", "H3_RESOLUTION": 5, "CENTROID_LAT": 48.0,
            "CENTROID_LON": -123.0, "SUPPORT_STATE": "MODEL_BBOX_CENTROID",
        }], schema=SUPPORT_SCHEMA),
        SUPPORT_SCHEMA,
    )
    manifest = tmp_path / "MANIFEST.json"
    write_manifest(
        manifest,
        manifest_payload(
            product="meteorological.spatial_support",
            run_id="historical",
            config_path=tmp_path / "config.yaml",
            resolved_config={"bbox": "historical"},
            artifacts=[parquet_contract(artifact)],
            sources=[{
                "name": "fixture", "license": "fixture", "attribution": "fixture",
                "observation_period": "not applicable", "redistribution_restrictions": "none",
            }],
            method_version="h3_bbox_centroid_support_v1",
        ),
    )
    assert load_manifest(manifest, verify_artifacts=True)["method_version"] == "h3_bbox_centroid_support_v1"
    with pytest.raises(ValueError, match="archived deep validator"):
        validate_product(manifest)


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


@pytest.mark.parametrize("run_id", ["../outside", "/tmp/outside", "..", "a/b", "a\\b"])
def test_publisher_rejects_run_id_path_escape(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError, match="safe path component"):
        TransactionalFamilyPublisher(tmp_path / "publication", run_id=run_id)


def test_publisher_preserves_other_staging_and_rejects_symlink(tmp_path: Path) -> None:
    parent = tmp_path / "publication"
    old = parent / ".staging" / "previous"
    old.mkdir(parents=True)
    (old / "evidence.txt").write_text("keep")
    with TransactionalFamilyPublisher(parent, run_id="previous"):
        assert (old / "evidence.txt").read_text() == "keep"
    link_parent = tmp_path / "linked"
    link_parent.mkdir()
    (link_parent / ".staging").symlink_to(parent / ".staging", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        with TransactionalFamilyPublisher(link_parent, run_id="new"):
            pass


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
    assert not (archive / "MIGRATION_JOURNAL.json").exists()
    assert json.loads((archive / "MIGRATION_RECOVERY.json").read_text())["state"] == "ROLLED_BACK"


def test_legacy_recovery_preserves_different_conflicting_copies(tmp_path: Path) -> None:
    source = tmp_path / "data/legacy/product.txt"
    source.parent.mkdir(parents=True)
    source.write_text("regenerated")
    archive = tmp_path / "migrations/run"
    destination = archive / "data/legacy/product.txt"
    destination.parent.mkdir(parents=True)
    destination.write_text("original")
    journal = archive / "MIGRATION_JOURNAL.json"
    journal.write_text(json.dumps({"state": "MOVING", "artifacts": [{
        "source": str(source), "destination": str(destination),
        "checksum": checksum_path(destination), "moved": True,
    }]}))
    with pytest.raises(RuntimeError, match="both copies and journal retained"):
        _recover_interrupted_migrations(tmp_path / "migrations")
    assert source.read_text() == "regenerated"
    assert destination.read_text() == "original"
    assert json.loads(journal.read_text())["state"] == "CONFLICT"


def test_legacy_recovery_rejects_archive_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("original")
    archive = tmp_path / "migrations/run"
    archive.mkdir(parents=True)
    destination = archive / "link.txt"
    destination.symlink_to(outside)
    source = tmp_path / "data/legacy/product.txt"
    journal = archive / "MIGRATION_JOURNAL.json"
    journal.write_text(json.dumps({"state": "MOVING", "artifacts": [{
        "source": str(source), "destination": str(destination),
        "checksum": checksum_path(outside), "moved": True,
    }]}))
    with pytest.raises(RuntimeError, match="both copies and journal retained"):
        _recover_interrupted_migrations(tmp_path / "migrations")
    assert outside.read_text() == "original"
    assert destination.is_symlink()
    assert json.loads(journal.read_text())["state"] == "CONFLICT"


def test_legacy_recovery_records_equal_copies_without_erasing_archive(tmp_path: Path) -> None:
    source = tmp_path / "data/legacy/product.txt"
    source.parent.mkdir(parents=True)
    source.write_text("same")
    archive = tmp_path / "migrations/run"
    destination = archive / "data/legacy/product.txt"
    destination.parent.mkdir(parents=True)
    destination.write_text("same")
    (archive / "MIGRATION_JOURNAL.json").write_text(json.dumps({"state": "MOVING", "artifacts": [{
        "source": str(source), "destination": str(destination),
        "checksum": checksum_path(destination), "moved": True,
    }]}))
    _recover_interrupted_migrations(tmp_path / "migrations")
    assert source.read_text() == destination.read_text() == "same"
    assert json.loads((archive / "MIGRATION_RECOVERY.json").read_text())["state"] == "ROLLED_BACK"


def test_legacy_recovery_discovers_custom_archive_root(tmp_path: Path) -> None:
    source = tmp_path / "data/legacy/product.txt"
    archive = tmp_path / "custom-archive"
    destination = archive / "data/legacy/product.txt"
    destination.parent.mkdir(parents=True)
    destination.write_text("original")
    (archive / "MIGRATION_JOURNAL.json").write_text(json.dumps({"state": "MOVING", "artifacts": [{
        "source": str(source), "destination": str(destination),
        "checksum": checksum_path(destination), "moved": True,
    }]}))
    _recover_interrupted_migrations(tmp_path / "migrations", archive_root=archive)
    assert source.read_text() == "original"
    assert (archive / "MIGRATION_RECOVERY.json").exists()


def test_legacy_recovery_handles_move_before_and_after_journal_update(tmp_path: Path) -> None:
    archive = tmp_path / "migrations/run"
    moved_source = tmp_path / "data/legacy/moved.txt"
    moved_destination = archive / "data/legacy/moved.txt"
    moved_destination.parent.mkdir(parents=True)
    moved_destination.write_text("first")
    pending_source = tmp_path / "data/legacy/pending.txt"
    pending_source.parent.mkdir(parents=True)
    pending_source.write_text("second")
    (archive / "MIGRATION_JOURNAL.json").write_text(json.dumps({
        "state": "MOVING",
        "artifacts": [
            {"source": str(moved_source), "destination": str(moved_destination),
             "checksum": checksum_path(moved_destination), "moved": False},
            {"source": str(pending_source),
             "destination": str(archive / "data/legacy/pending.txt"),
             "checksum": checksum_path(pending_source), "moved": False},
        ],
    }))
    _recover_interrupted_migrations(tmp_path / "migrations")
    assert moved_source.read_text() == "first"
    assert pending_source.read_text() == "second"
    assert json.loads((archive / "MIGRATION_RECOVERY.json").read_text())["state"] == "ROLLED_BACK"


def test_legacy_migration_lock_rejects_overlapping_owners(tmp_path: Path) -> None:
    with _migration_lock(tmp_path / "migrations"):
        with pytest.raises(RuntimeError, match="already active"):
            with _migration_lock(tmp_path / "migrations"):
                pass


def test_complete_legacy_migration_journal_is_finalized(tmp_path: Path) -> None:
    archive = tmp_path / "migrations/run"
    archive.mkdir(parents=True)
    journal = archive / "MIGRATION_JOURNAL.json"
    journal.write_text(json.dumps({"state": "COMPLETE", "artifacts": []}))
    _recover_interrupted_migrations(tmp_path / "migrations")
    assert not journal.exists()
    assert json.loads((archive / "MIGRATION_MANIFEST.json").read_text())["state"] == "COMPLETE"
