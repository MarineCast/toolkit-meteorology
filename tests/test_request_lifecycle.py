"""Operational lifecycle regressions through real budgets, caches and getters."""

import json
from pathlib import Path

import pytest
from meteorology.era5.resources import Limits
from meteorology.request_storage import RequestBudget, CONTROL_BYTES
from .request_provider import install_provider
from .test_request_review import temperature


def test_explicit_retention_survives_same_run_reopen(tmp_path):
    limits = Limits(staging_bytes=100000)
    run = tmp_path / "runs/small"
    budget = RequestBudget(limits, namespace=tmp_path, run_root=run, rss=lambda: 0)
    file = run / "retained.bin"
    with budget.write_file(file, 30000):
        file.write_bytes(b"x" * 30000)
    budget.retain_directory(run)
    assert budget.receipt()["storage"]["run_staging_bytes"] == CONTROL_BYTES
    for _ in range(3):
        reopened = RequestBudget(limits, namespace=tmp_path, run_root=run, rss=lambda: 0)
        assert reopened.receipt()["storage"]["run_staging_bytes"] == CONTROL_BYTES
        reopened.check_disk(run, additional_bytes=5000)
    assert file.read_bytes() == b"x" * 30000


def test_reaggregation_after_refresh_has_its_own_execution(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    preserved = {str(p): p.read_bytes() for p in (cache / "real/runs").rglob("*.parquet")}
    preserved.update({str(p): p.read_bytes() for p in old.directory.iterdir()})
    provider.temperature = 293.15
    refreshed = temperature(cache, frequency="hourly", refresh=True)
    calls = len(provider.calls)
    daily = temperature(cache, limits=Limits(memory_bytes=1024**3, requests=1))
    assert len(provider.calls) == calls
    assert daily.to_pandas().VALUE.tolist() == pytest.approx([20] * 4, abs=0.001)
    assert daily.metadata["source_generation"] == refreshed.metadata["source_generation"]
    assert daily.metadata["budget"]["requests"] == 0
    assert all(Path(path).read_bytes() == contents for path, contents in preserved.items())


def test_retained_reconciliation_is_stable_and_quota_includes_bytes(tmp_path):
    limits = Limits(staging_bytes=100000)
    run = tmp_path / "runs/stable"
    b = RequestBudget(limits, namespace=tmp_path, run_root=run, rss=lambda: 0)
    path = run / "retained"
    with b.write_file(path, 30000):
        path.write_bytes(b"x" * 30000)
    b.retain_directory(run)
    initial = b.receipt()["storage"]["namespace_bytes"]
    for _ in range(3):
        b = RequestBudget(
            limits, namespace=tmp_path, run_root=run, rss=lambda: 0, retained_cache_bytes=100000
        )
        assert b.receipt()["storage"]["namespace_bytes"] == initial
        assert b.receipt()["storage"]["run_staging_bytes"] == CONTROL_BYTES
    with pytest.raises(ValueError, match="Retained-cache quota"):
        b.begin_write(run / "new", 5000)


def test_corrupt_retention_record_requires_reconciliation(tmp_path):
    run = tmp_path / "runs/corrupt"
    b = RequestBudget(Limits(), namespace=tmp_path, run_root=run, rss=lambda: 0)
    path = run / "data"
    with b.write_file(path, 4):
        path.write_bytes(b"data")
    b.retain_directory(run)
    marker = run / "RETAINED_STORAGE.json"
    data = json.loads(marker.read_text())
    data["paths"] = []
    marker.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="lifecycle evidence"):
        RequestBudget(Limits(), namespace=tmp_path, run_root=run, rss=lambda: 0)
    assert path.read_bytes() == b"data"


def test_same_generation_retry_keeps_transfer_and_request_reservations(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    caps = Limits(memory_bytes=1024**3, requests=2)
    cache = tmp_path / "cache"
    provider.fail = True
    with pytest.raises(RuntimeError):
        temperature(cache, limits=caps)
    journals = list((cache / "real/runs").glob("*/TRANSFER_BUDGET.json"))
    assert len(journals) == 1
    original = json.loads(journals[0].read_text())
    calls = len(provider.calls)
    provider.fail = False
    with pytest.raises(ValueError, match="request cap"):
        temperature(cache, limits=caps)
    final = json.loads(journals[0].read_text())
    assert final["requests"] == original["requests"] == 2
    assert final["reserved_transfer_bytes"] == original["reserved_transfer_bytes"]
    assert len(provider.calls) == calls
    assert len(list((cache / "real/runs").glob("*/TRANSFER_BUDGET.json"))) == 1


def test_concurrent_reaggregation_shares_new_execution(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    temperature(cache)
    fresh = temperature(cache, frequency="hourly", refresh=True)
    caps = Limits(memory_bytes=1024**3, requests=1)
    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(lambda _: temperature(cache, limits=caps), range(2)))
    assert a.metadata["result_id"] == b.metadata["result_id"]
    assert sum(result.cache_hit for result in (a, b)) == 1
    states = [
        json.loads(path.read_text()) for path in (cache / "real/runs").glob("*/RUN_STATE.json")
    ]
    current = [
        state
        for state in states
        if state.get("source_generation") == fresh.metadata["source_generation"]
        and state.get("result_id") == a.metadata["result_id"]
    ]
    assert len(current) == 1
