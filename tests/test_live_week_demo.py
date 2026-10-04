"""Offline request-budget and demo planning regressions; never contact NOAA."""
import io
import json
from pathlib import Path

import pytest

from meteorology.hourly_weather.live import FIELDS, TransferBudget, selected_ranges
from meteorology.week_demo import run


class Response(io.BytesIO):
    def __init__(self, body=b"abcd", status=206, content_range="bytes 10-13/100"):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body)), "Content-Range": content_range}


class Opener:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        return self.response


def test_budget_survives_restart_and_blocks_before_network(tmp_path):
    path = tmp_path / "budget.json"
    budget = TransferBudget(path, max_requests=1, max_bytes=4)
    opener = budget.opener = Opener(Response())
    budget.get("https://example.invalid", tmp_path / "body", limit=4, byte_range=(10, 13))
    assert opener.calls == 1
    assert (tmp_path / "body").read_bytes() == b"abcd"
    resumed = TransferBudget(path, max_requests=1, max_bytes=4)
    resumed.opener = opener
    with pytest.raises(ValueError, match="exhausted"):
        resumed.get("https://example.invalid", tmp_path / "body", limit=1)
    assert opener.calls == 1
    assert resumed.state["received_bytes"] == 4
    with pytest.raises(ValueError, match="original"):
        TransferBudget(path, max_requests=2, max_bytes=4)


@pytest.mark.parametrize("response", [Response(status=200), Response(content_range="bytes 9-12/100"), Response(body=b"abcde")])
def test_rejected_response_keeps_reservation(tmp_path, response):
    path = tmp_path / "budget.json"
    budget = TransferBudget(path, max_requests=2, max_bytes=10)
    budget.opener = Opener(response)
    with pytest.raises(ValueError):
        budget.get("https://example.invalid", tmp_path / "body", limit=4, byte_range=(10, 13))
    state = json.loads(path.read_text())
    assert state["reserved_bytes"] == 4
    assert state["requests"] == 1
    assert state["received_bytes"] == 0
    assert state["transfers"][0]["status"] == "failed_or_interrupted"


def test_exact_unique_inventory_selection():
    lines = [f"{i+1}:{100*i}:d=2024010100{selector}anl:" for i, selector in enumerate(FIELDS.values())]
    lines.append("9:800:d=2024010100:OTHER:surface:anl:")
    assert selected_ranges("\n".join(lines)) == [(i*100, i*100+99) for i in range(8)]
    with pytest.raises(ValueError, match="ambiguous"):
        selected_ranges("\n".join(lines + ["10:900:d=x:TMP:2 m above ground:anl:"]))
    with pytest.raises(ValueError, match="unbounded"):
        selected_ranges("\n".join(lines[:-1]))


def test_dry_run_is_network_and_write_free_and_handles_dst(tmp_path):
    root = tmp_path / "absent"
    plan = run(root, start_date="2024-03-04", dry_run=True)
    assert plan["source_cycles"] == 167
    assert plan["planned_http_requests"] == 1503
    assert plan["network_requests_issued"] == 0
    assert not root.exists()
    with pytest.raises(ValueError, match="Monday"):
        run(Path("unused"), start_date="2024-01-02", dry_run=True)


def test_invalid_range_cannot_consume_budget(tmp_path):
    budget = TransferBudget(tmp_path / "budget.json", max_requests=2, max_bytes=10)
    budget.opener = Opener(Response())
    with pytest.raises(ValueError, match="exact"):
        budget.get("https://example.invalid", tmp_path / "body", limit=3, byte_range=(10, 13))
    assert budget.opener.calls == 0
    assert not budget.path.exists()


def test_demo_restores_workspace_and_reports_missing_decoder(tmp_path, monkeypatch):
    import meteorology.week_demo as demo

    def unavailable(*args, **kwargs):
        raise ImportError("test missing decoder")

    monkeypatch.setenv("METEOROLOGY_WORKSPACE", "original-workspace")
    monkeypatch.setattr(demo, "fetch_hour", unavailable)
    with pytest.raises(ImportError, match="test missing decoder"):
        run(tmp_path / "run")
    import os
    assert os.environ["METEOROLOGY_WORKSPACE"] == "original-workspace"
    report = json.loads((tmp_path / "run/REPORT.json").read_text())
    assert report["status"] == "INCOMPLETE"
    assert report["transfer_requests"] == 0
    assert report["invocations"][0]["status"] == "INCOMPLETE"
    with pytest.raises(ValueError, match="same dates"):
        run(tmp_path / "run", max_requests=1700)
    assert os.environ["METEOROLOGY_WORKSPACE"] == "original-workspace"


def test_interrupted_request_reserves_budget(tmp_path):
    budget = TransferBudget(tmp_path / "budget.json", max_requests=1, max_bytes=4)

    class Interrupted:
        def open(self, *args, **kwargs):
            raise KeyboardInterrupt()

    budget.opener = Interrupted()
    with pytest.raises(KeyboardInterrupt):
        budget.get("https://example.invalid", tmp_path / "body", limit=4)
    resumed = TransferBudget(budget.path, max_requests=1, max_bytes=4)
    assert resumed.state["reserved_bytes"] == 4
    with pytest.raises(ValueError, match="exhausted"):
        resumed.get("https://example.invalid", tmp_path / "body", limit=4)


def test_second_demo_writer_is_rejected_before_requests(tmp_path):
    import fcntl

    root = tmp_path / "run"
    root.mkdir()
    with (root / ".demo.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="Another writer"):
            run(root)
    assert not (root / "REQUEST.json").exists()
