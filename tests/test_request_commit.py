import json
from pathlib import Path
import pytest
from meteorology.era5.resources import Limits
from meteorology.request_storage import RequestBudget
from .request_provider import install_provider
from .test_request_review import temperature


def test_post_pointer_accounting_failure_returns_committed_result(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    original = RequestBudget.finish_write

    def fail_after_pointer(self, path):
        if path.name == "ACTIVE_GENERATIONS.json":
            raise OSError("injected accounting after pointer replacement")
        return original(self, path)

    monkeypatch.setattr(RequestBudget, "finish_write", fail_after_pointer)
    provider.temperature = 293.15
    result = temperature(cache, refresh=True)
    assert result.metadata["operational_warnings"]
    assert result.to_pandas().VALUE.tolist() == pytest.approx([20] * 4, abs=0.001)
    assert temperature(cache).metadata["result_id"] == result.metadata["result_id"]
    for state_path in (cache / "real/runs").glob("*/RUN_STATE.json"):
        state = json.loads(state_path.read_text())
        if state.get("result_id") == result.metadata["result_id"]:
            assert state["status"] != "INCOMPLETE_NOT_PUBLISHED"
    assert temperature(
        cache, result_id=old.metadata["result_id"]
    ).to_pandas().VALUE.tolist() == pytest.approx([10] * 4, abs=0.001)


def assert_complete_active(cache, expected):
    from meteorology.request_cache import _manifest, _validate
    from meteorology.request_vintage import read_index

    index = read_index(cache / "real")
    witnesses = [
        entry for scope in index["scopes"].values() for entry in scope["coverage"].values()
    ]
    assert {entry["result_id"] for entry in witnesses} == {expected.metadata["result_id"]}
    path, manifest = _manifest(cache / "real", expected.metadata["result_id"])
    _validate(path.parent, manifest)


@pytest.mark.parametrize(
    "stage", ["history_retention", "index_retention", "post_commit_persist", "commit_receipt_write"]
)
def test_confirmed_commit_maintenance_failures_warn_and_reconcile(tmp_path, monkeypatch, stage):
    from meteorology import request_lifecycle as lifecycle

    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    provider.temperature = 293.15
    with monkeypatch.context() as injection:
        if stage.endswith("_retention"):
            original = RequestBudget.retain_directory

            def fail(self, path):
                if (
                    getattr(self, "publication", None)
                    and self.publication.phase == "COMMITTED"
                    and path.name
                    == (
                        "generations" if stage == "history_retention" else "ACTIVE_GENERATIONS.json"
                    )
                ):
                    raise OSError("injected retention failure")
                return original(self, path)

            injection.setattr(RequestBudget, "retain_directory", fail)
        elif stage == "post_commit_persist":
            original = RequestBudget.persist

            def fail(self):
                if getattr(self, "publication", None) and self.publication.phase == "COMMITTED":
                    raise OSError("injected post-commit persist failure")
                return original(self)

            injection.setattr(RequestBudget, "persist", fail)
        else:
            original = lifecycle.Publication.save_outcome

            def fail(self, status, warnings):
                if status == "RECONCILED":
                    raise OSError("injected final receipt failure")
                return original(self, status, warnings)

            injection.setattr(lifecycle.Publication, "save_outcome", fail)
        result = temperature(cache, refresh=True)
    assert result.metadata["publication_status"] == "COMMITTED_WITH_OPERATIONAL_WARNING"
    assert result.metadata["operational_warnings"]
    assert_complete_active(cache, result)
    journal_paths = list((cache / "real/runs").glob("*/TRANSFER_BUDGET.json"))
    counters = {str(path): path.read_bytes() for path in journal_paths}
    calls = len(provider.calls)
    clean = temperature(cache)
    assert clean.metadata["result_id"] == result.metadata["result_id"]
    assert not clean.metadata["operational_warnings"]
    clean.reconcile().reconcile()
    assert len(provider.calls) == calls
    assert all(Path(path).read_bytes() == content for path, content in counters.items())
    assert temperature(
        cache, result_id=old.metadata["result_id"]
    ).to_pandas().VALUE.tolist() == pytest.approx([10] * 4, abs=0.001)


def test_directory_fsync_failure_is_uncertain_then_recovered(tmp_path, monkeypatch):
    import meteorology.era5.resources as resources
    from meteorology.request_lifecycle import PublicationUncertainError, read_record

    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    provider.temperature = 293.15
    replaced = [False]
    real_replace, real_fsync = resources.os.replace, resources.os.fsync

    def replace(source, destination):
        real_replace(source, destination)
        if Path(destination).name == "ACTIVE_GENERATIONS.json":
            replaced[0] = True

    def fsync(fd):
        if replaced[0]:
            replaced[0] = False
            raise OSError("injected pointer-directory fsync failure")
        real_fsync(fd)

    with monkeypatch.context() as injection:
        injection.setattr(resources.os, "replace", replace)
        injection.setattr(resources.os, "fsync", fsync)
        with pytest.raises(PublicationUncertainError, match="uncertain"):
            temperature(cache, refresh=True)
    record = read_record(cache / "real/REQUEST_PUBLICATION.json")
    assert record["status"] == "UNCERTAIN"
    assert record["result_id"] != old.metadata["result_id"]
    recovered = temperature(cache)
    assert recovered.metadata["result_id"] == record["result_id"]
    assert_complete_active(cache, recovered)
    assert read_record(cache / "real/REQUEST_PUBLICATION.json")["status"] == "RECONCILED"


def test_secondary_state_write_failure_preserves_primary_exception(tmp_path, monkeypatch):
    from meteorology.request_lifecycle import read_record

    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    original = RequestBudget.write_json

    def fail(self, path, value):
        if path.name == "ACTIVE_GENERATIONS.json":
            raise OSError("primary before-pointer failure")
        if path.name == "RUN_STATE.json" and value.get("status") == "INCOMPLETE_NOT_PUBLISHED":
            raise RuntimeError("secondary state failure")
        return original(self, path, value)

    monkeypatch.setattr(RequestBudget, "write_json", fail)
    provider.temperature = 293.15
    with pytest.raises(OSError, match="primary before-pointer") as error:
        temperature(cache, refresh=True)
    assert any("Secondary lifecycle" in note for note in error.value.__notes__)
    assert read_record(cache / "real/REQUEST_PUBLICATION.json")["status"] == "NOT_ACTIVE"
    assert temperature(cache).metadata["result_id"] == old.metadata["result_id"]


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_post_commit_interruption_is_propagated_and_restart_repairs(
    tmp_path, monkeypatch, interruption
):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    provider.temperature = 293.15
    original = RequestBudget.finish_write

    def stop(self, path):
        if path.name == "ACTIVE_GENERATIONS.json":
            raise interruption()
        return original(self, path)

    with monkeypatch.context() as injection:
        injection.setattr(RequestBudget, "finish_write", stop)
        with pytest.raises(interruption):
            temperature(cache, refresh=True)
    recovered = temperature(cache)
    assert recovered.metadata["result_id"] != old.metadata["result_id"]
    assert recovered.to_pandas().VALUE.tolist() == pytest.approx([20] * 4, abs=0.001)
    assert_complete_active(cache, recovered)


def test_subprocess_exit_after_replacement_recovers_from_durable_intent(tmp_path):
    import subprocess
    import sys
    from meteorology import get_temperature
    from .request_fixtures import era5_grib
    from .test_request_api import BBOX, RETRIEVED

    source = era5_grib(tmp_path / "old.grib")
    revised = era5_grib(tmp_path / "new.grib", samples_override={"t2m": 293.15})
    cache = tmp_path / "cache"
    old = get_temperature(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        source_files=[source],
        retrieved_at_utc=RETRIEVED,
        cache_dir=cache,
    )
    script = """import os,sys
from pathlib import Path
from meteorology import get_temperature
from meteorology.era5 import resources
real=resources.os.replace
def exit_at_pointer(source,destination):
    real(source,destination)
    if Path(destination).name=='ACTIVE_GENERATIONS.json':
        os._exit(73)
resources.os.replace=exit_at_pointer
get_temperature('2024-01-01','2024-01-01',(-125.25,49,-125,49.25),'era5',
                source_files=[sys.argv[1]],retrieved_at_utc='2025-01-01T00:00:00Z',cache_dir=sys.argv[2],refresh=True)
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(revised), str(cache)],
        timeout=30,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 73, process.stderr
    from meteorology.request_lifecycle import read_record

    record = read_record(cache / "fixtures/REQUEST_PUBLICATION.json")
    assert record["status"] == "PREPARED"
    recovered = get_temperature(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        source_files=[revised],
        retrieved_at_utc=RETRIEVED,
        cache_dir=cache,
    )
    assert recovered.metadata["result_id"] != old.metadata["result_id"]
    assert recovered.to_pandas().VALUE.tolist() == pytest.approx([20] * 4, abs=0.001)
    assert read_record(cache / "fixtures/REQUEST_PUBLICATION.json")["status"] == "RECONCILED"


def test_promotion_helper_witness_survives_its_accounting_exception(tmp_path, monkeypatch):
    from meteorology.request import plan_weather
    from meteorology.request_cache import _manifest
    from meteorology.request_lifecycle import Publication, read_record
    from meteorology.request_vintage import promote
    from .test_request_api import BBOX
    import meteorology.request_cache as cache_module

    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    provider.temperature = 293.15
    with monkeypatch.context() as injection:

        def not_yet(*args, **kwargs):
            raise OSError("prepare a validated result before helper test")

        injection.setattr(cache_module, "promote", not_yet)
        with pytest.raises(OSError):
            temperature(cache, refresh=True)
    root = cache / "real"
    identity = next(
        p.name for p in (root / "results").iterdir() if p.name != old.metadata["result_id"]
    )
    path, manifest = _manifest(root, identity)
    state_path = next(
        p
        for p in (root / "runs").glob("*/RUN_STATE.json")
        if read_record(p)["source_generation"] == manifest["source_generation"]
    )
    state = read_record(state_path)
    budget = RequestBudget(
        Limits(
            memory_bytes=1024**3,
            staging_bytes=2 * 1024**3,
            transfer_bytes=512 * 1024**2,
            requests=1024,
        ),
        namespace=root,
        run_root=state_path.parent,
    )
    binding = {key: state[key] for key in ("request_id", "execution_id", "source_generation")}
    publication = Publication(root, budget, binding)
    original = RequestBudget.finish_write

    def fail(self, path):
        if path.name == "ACTIVE_GENERATIONS.json":
            raise OSError("helper accounting failure")
        return original(self, path)

    monkeypatch.setattr(RequestBudget, "finish_write", fail)
    plan = plan_weather(["temperature"], "2024-01-01", "2024-01-01", BBOX, "era5", cache_dir=cache)
    with pytest.raises(OSError, match="helper accounting"):
        promote(plan, root, manifest, refresh=True, budget=budget, publication=publication)
    assert publication.outcome() == "COMMITTED"
    assert read_record(root / "REQUEST_PUBLICATION.json")["status"] == "COMMITTED"
    selected = temperature(cache)
    assert selected.metadata["result_id"] == identity
    assert selected.metadata["operational_warnings"]


def test_refresh_interruption_resumes_same_identity_and_consumed_cap(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    caps = Limits(memory_bytes=1024**3, requests=2)

    def interrupt(dataset, selection):
        raise KeyboardInterrupt()

    monkeypatch.setattr(provider, "submit", interrupt)
    with pytest.raises(KeyboardInterrupt):
        temperature(cache, refresh=True, limits=caps)
    from meteorology.request_lifecycle import read_record

    profile = next((cache / "real/refresh-executions").glob("*.json"))
    before = read_record(profile)
    with pytest.raises(ValueError, match="request cap"):
        temperature(cache, refresh=True, limits=caps)
    after = read_record(profile)
    assert before["execution_id"] == after["execution_id"]
    assert before["source_generation"] == after["source_generation"]
    journals = list((cache / "real/runs").glob("*/TRANSFER_BUDGET.json"))
    assert len(journals) == 1
    assert json.loads(journals[0].read_text())["requests"] == 2


def test_durable_pointer_with_failed_commit_receipt_still_returns_warning(tmp_path, monkeypatch):
    import meteorology.request_lifecycle as lifecycle

    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    original = lifecycle.write_record

    def fail(path, value, budget=None):
        if (
            path.name == "REQUEST_PUBLICATION.json"
            and value.get("status") == "COMMITTED"
            and budget is None
        ):
            raise OSError("injected durable receipt persistence failure")
        return original(path, value, budget)

    provider.temperature = 293.15
    with monkeypatch.context() as injection:
        injection.setattr(lifecycle, "write_record", fail)
        result = temperature(cache, refresh=True)
    assert result.metadata["publication_status"] == "COMMITTED_WITH_OPERATIONAL_WARNING"
    assert result.metadata["operational_warnings"]
    assert_complete_active(cache, result)
    repaired = temperature(cache)
    assert repaired.metadata["result_id"] == result.metadata["result_id"]
    assert temperature(
        cache, result_id=old.metadata["result_id"]
    ).to_pandas().VALUE.tolist() == pytest.approx([10] * 4, abs=0.001)


def test_before_pointer_failure_retries_without_resetting_acquisition(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    provider.temperature = 293.15
    original = RequestBudget.write_json

    def fail(self, path, value):
        if path.name == "ACTIVE_GENERATIONS.json":
            raise OSError("injected before-pointer failure")
        return original(self, path, value)

    with monkeypatch.context() as injection:
        injection.setattr(RequestBudget, "write_json", fail)
        with pytest.raises(OSError):
            temperature(cache, refresh=True)
    calls = len(provider.calls)
    result = temperature(cache, refresh=True)
    assert len(provider.calls) == calls
    assert result.metadata["budget"]["requests"] == 5
    assert result.metadata["result_id"] != old.metadata["result_id"]
    assert_complete_active(cache, result)
