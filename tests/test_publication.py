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


def test_abrupt_exit_reclaims_only_marked_staging(tmp_path):
    import subprocess
    import sys
    control = tmp_path/'control'
    unowned = control/'.staging/legacy'
    unowned.mkdir(parents=True)
    (unowned/'keep').write_text('user file')
    script = '''
import os,sys
from pathlib import Path
from meteorology.core.artifacts import TransactionalFamilyPublisher
root=Path(sys.argv[1])
with TransactionalFamilyPublisher(root/'control') as publisher:
    publisher.stage_path(root/'out/data').write_text('intermediate')
    os._exit(17)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)])
    assert result.returncode == 17
    markers = list((control/'.staging').glob('*.owner.json'))
    assert len(markers) == 1
    candidate = markers[0].with_name(markers[0].name.removesuffix('.owner.json'))
    assert candidate.is_dir()
    with TransactionalFamilyPublisher(control) as publisher:
        assert not markers[0].exists() and not candidate.exists()
        publisher.stage_path(tmp_path/'out/data').write_text('final')
        publisher.publish()
    assert (tmp_path/'out/data').read_text() == 'final'
    assert (unowned/'keep').read_text() == 'user file'
    assert list((control/'.staging').iterdir()) == [unowned]


@pytest.mark.parametrize('target', ['staging', 'backups'])
def test_cleanup_failure_reports_commit_and_recovers(tmp_path, monkeypatch, target):
    from meteorology.core.artifacts import publication
    output = tmp_path/'out/data'
    output.parent.mkdir()
    output.write_text('old')
    original = publication.shutil.rmtree
    def fail(path, *args, **kwargs):
        path = Path(path)
        if (target == 'staging' and path.parent.name == '.staging') or path.name == target:
            raise PermissionError('injected deletion failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(publication.shutil, 'rmtree', fail)
    with pytest.raises(RuntimeError, match='committed; .*cleanup incomplete'):
        with TransactionalFamilyPublisher(tmp_path/'control') as publisher:
            publisher.stage_path(output).write_text('new')
            publisher.publish()
    assert output.read_text() == 'new'
    if target == 'backups':
        assert json.loads(publisher.journal.read_text())['phase'] == 'committed'
    assert publisher.staging_owner.exists()
    monkeypatch.setattr(publication.shutil, 'rmtree', original)
    with TransactionalFamilyPublisher(tmp_path/'control'):
        assert not publisher.transaction.exists()
        assert not publisher.staging.exists()
    assert output.read_text() == 'new'


def test_staging_cleanup_does_not_hide_original_failure(tmp_path, monkeypatch):
    from meteorology.core.artifacts import publication
    original = publication.shutil.rmtree
    def fail(path, *args, **kwargs):
        if Path(path).parent.name == '.staging':
            raise PermissionError('injected deletion failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(publication.shutil, 'rmtree', fail)
    with pytest.raises(ValueError, match='original failure') as error:
        with TransactionalFamilyPublisher(tmp_path/'control') as publisher:
            publisher.stage_path(tmp_path/'data').write_text('candidate')
            raise ValueError('original failure')
    assert any('staging cleanup incomplete' in note for note in error.value.__notes__)
    assert publisher.staging_owner.exists()


def test_mismatched_staging_owner_is_preserved(tmp_path):
    control = tmp_path/'control'
    token = 'a'*32
    candidate = control/'.staging'/token
    candidate.mkdir(parents=True)
    (candidate/'keep').write_text('preserve')
    marker = candidate.with_suffix('.owner.json')
    marker.write_text(json.dumps(dict(owner='meteorology-publication-staging-v1',
                                      control=str(tmp_path/'different'), token=token)))
    with pytest.raises(RuntimeError, match='ownership mismatch'):
        with TransactionalFamilyPublisher(control):
            pass
    assert (candidate/'keep').read_text() == 'preserve'
    assert marker.exists()
