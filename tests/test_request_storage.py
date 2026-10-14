from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections import namedtuple
import json
import pytest
from meteorology.era5.resources import Limits, LimitExceeded, _bounded_transfer_locked
from meteorology.request_storage import RequestBudget, CONTROL_BYTES
from .test_era5_resources import Response
from .test_request_api import BBOX, fixture_options
from meteorology import get_temperature


def budget(root, run="one", **kwargs):
    return RequestBudget(
        Limits(staging_bytes=kwargs.pop("staging_bytes", 1024**2)),
        namespace=root,
        run_root=root / "runs" / run,
        rss=lambda: 0,
        **kwargs,
    )


def test_retained_history_does_not_consume_new_run_staging(tmp_path):
    opts = fixture_options(tmp_path)
    namespace = opts["cache_dir"] / "fixtures"
    namespace.mkdir(parents=True)
    (namespace / "historical-evidence").write_bytes(b"x" * (2 * 1024**2))
    result = get_temperature(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        limits=Limits(memory_bytes=1024**3, staging_bytes=1024**2),
        **opts,
    )
    assert result.metadata["status"] == "COMPLETE"
    assert (namespace / "historical-evidence").stat().st_size == 2 * 1024**2
    assert result.metadata["budget"]["storage"]["startup_reconciliations"] == 1


def test_staging_and_retention_reservations_fail_before_write(tmp_path):
    b = budget(tmp_path, staging_bytes=100000)
    with pytest.raises(LimitExceeded, match="staging"):
        b.begin_write(tmp_path / "runs/one/too-large", 40000)
    assert not (tmp_path / "runs/one/too-large").exists()
    b = budget(tmp_path, "two", retained_cache_bytes=100000)
    with pytest.raises(LimitExceeded, match="Retained-cache"):
        b.begin_write(tmp_path / "sources/too-large", 40000)
    assert not (tmp_path / "sources/too-large").exists()


def test_physical_disk_safeguard(tmp_path, monkeypatch):
    b = budget(tmp_path)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        "meteorology.request_storage.shutil.disk_usage", lambda _: Usage(100, 99, 1)
    )
    with pytest.raises(LimitExceeded, match="physical disk"):
        b.begin_write(tmp_path / "file", 1)


def test_resumed_partial_is_counted_and_unused_reservation_remains(tmp_path):
    b = budget(tmp_path)
    path = tmp_path / "sources/partial"
    path.parent.mkdir()
    b.begin_write(path, 50000)
    path.write_bytes(b"x" * 10000)
    b.progress(path, 10000)
    resumed = budget(tmp_path)
    state = resumed.receipt()["storage"]
    assert state["run_staging_bytes"] == CONTROL_BYTES + 50000
    assert state["outstanding_reservation_bytes"] == 40000
    resumed.finish_write(path)
    assert resumed.receipt()["storage"]["run_staging_bytes"] == CONTROL_BYTES + 10000


def test_concurrent_reservations_cannot_overcommit_quota(tmp_path):
    a = budget(tmp_path, "a", retained_cache_bytes=200000)
    b = budget(tmp_path, "b", retained_cache_bytes=200000)

    def reserve(item):
        worker, name = item
        try:
            worker.begin_write(tmp_path / name, 80000)
            return True
        except LimitExceeded:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(reserve, [(a, "first"), (b, "second")])) == [False, True]


def test_staging_retention_transition_preserves_bytes(tmp_path):
    b = budget(tmp_path)
    path = tmp_path / "runs/one/object"
    with b.write_file(path, 100):
        path.write_bytes(b"x" * 100)
    before = b.receipt()["storage"]
    destination = tmp_path / "retained-object"
    path.rename(destination)
    b.move_storage(path, destination)
    after = b.receipt()["storage"]
    assert after["namespace_bytes"] == before["namespace_bytes"]
    assert before["run_staging_bytes"] - after["run_staging_bytes"] == 100
    assert after["outstanding_reservation_bytes"] == 0


def test_transfer_chunks_and_json_writes_do_not_rescan_tree(tmp_path, monkeypatch):
    traversals = []
    original = Path.rglob

    def counted(path, *a, **kw):
        traversals.append(path)
        return original(path, *a, **kw)

    monkeypatch.setattr(Path, "rglob", counted)
    b = budget(tmp_path)
    payload = b"x" * (3 * 65536 + 7)
    _bounded_transfer_locked(
        lambda: Response(payload),
        tmp_path / "sources/object",
        budget=b,
        reservation_bytes=len(payload),
    )
    for i in range(25):
        b.write_json(tmp_path / "runs/one/point-block-state.json", dict(block=i))
    assert len(traversals) == 1
    assert b.received == len(payload)
    assert b.receipt()["storage"]["outstanding_reservation_bytes"] == 0
    assert (
        json.loads((tmp_path / "sources/object.receipt.json").read_text())["status"] == "COMPLETE"
    )


def test_public_processing_and_point_blocks_scan_only_at_start(tmp_path, monkeypatch):
    opts = fixture_options(tmp_path)
    traversals = []
    original = Path.rglob

    def counted(path, *a, **kw):
        traversals.append(path)
        return original(path, *a, **kw)

    monkeypatch.setattr(Path, "rglob", counted)
    get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", **opts)
    assert len(traversals) == 1
