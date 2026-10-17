import json
from pathlib import Path
import pytest
from meteorology.core.artifacts import TransactionalFamilyPublisher

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
