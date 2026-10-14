import pytest
from meteorology import get_weather, get_temperature
from .test_request_api import BBOX
from .request_provider import install_provider


def temperature(cache, **kwargs):
    return get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", cache_dir=cache, **kwargs)


def test_refresh_superset_wins_over_old_narrow_result(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    provider.temperature = 293.15
    refreshed = get_weather(
        ["temperature", "cloud_cover"],
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        cache_dir=cache,
        refresh=True,
    )
    selected = temperature(cache)
    assert selected.metadata["result_id"] == refreshed.metadata["result_id"]
    assert selected.to_pandas().VALUE.tolist() == pytest.approx([20] * 4, abs=0.001)
    assert old.to_pandas().VALUE.tolist() == pytest.approx([10] * 4, abs=0.001)


def test_equal_sized_revision_selection(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    temperature(cache)
    provider.temperature = 293.15
    new = temperature(cache, refresh=True)
    assert temperature(cache).metadata["result_id"] == new.metadata["result_id"]


def test_pin_and_overlapping_refresh_require_resolution(tmp_path, monkeypatch):
    from meteorology.request_cache import CacheError

    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = get_weather(
        ["temperature", "cloud_cover"], "2024-01-01", "2024-01-02", BBOX, "era5", cache_dir=cache
    )
    provider.temperature = 293.15
    new = temperature(cache, refresh=True)
    pin = temperature(cache, result_id=old.metadata["result_id"])
    assert pin.to_pandas().VALUE.tolist() == pytest.approx([10] * 4, abs=0.001)
    preview = get_temperature(
        "2024-01-01",
        "2024-01-02",
        BBOX,
        "era5",
        cache_dir=cache,
        result_id=old.metadata["result_id"],
        dry_run=True,
    )
    assert preview["cache"]["final_result_hit"]
    assert temperature(cache).metadata["result_id"] == new.metadata["result_id"]
    with pytest.raises(CacheError, match="incompatible source generations"):
        get_temperature("2024-01-01", "2024-01-02", BBOX, "era5", cache_dir=cache)
    with pytest.raises(CacheError, match="incompatible"):
        get_temperature(
            "2024-01-01",
            "2024-01-03",
            BBOX,
            "era5",
            cache_dir=cache,
            result_id=old.metadata["result_id"],
        )


def test_date_subset_after_multiday_refresh(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    temperature(cache)
    provider.temperature = 293.15
    new = get_weather(
        ["temperature", "cloud_cover"],
        "2024-01-01",
        "2024-01-02",
        BBOX,
        "era5",
        cache_dir=cache,
        refresh=True,
    )
    assert temperature(cache).metadata["result_id"] == new.metadata["result_id"]


def test_failed_and_partial_refresh_preserve_active(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    provider.fail = True
    with pytest.raises(RuntimeError):
        temperature(cache, refresh=True)
    provider.fail = False
    provider.missing_hour = 3
    partial = temperature(cache, refresh=True)
    assert partial.metadata["status"] == "PARTIAL"
    assert temperature(cache).metadata["result_id"] == old.metadata["result_id"]


@pytest.mark.parametrize("corruption", ["artifact", "manifest"])
def test_corrupt_active_fails_then_refresh_recovers(tmp_path, monkeypatch, corruption):
    from meteorology.request_cache import CacheError

    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    temperature(cache)
    provider.temperature = 293.15
    active = temperature(cache, refresh=True)
    artifact = active.directory / (
        active.metadata["artifacts"][0]["path"] if corruption == "artifact" else "MANIFEST.json"
    )
    artifact.write_bytes(b"CORRUPT")
    with pytest.raises(CacheError, match="corrupt"):
        temperature(cache)
    recovered = temperature(cache, refresh=True)
    assert temperature(cache).metadata["result_id"] == recovered.metadata["result_id"]
    assert artifact.read_bytes() == b"CORRUPT"


def test_reaggregation_preserves_source_vintage(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache, frequency="hourly")
    provider.temperature = 293.15
    new = temperature(cache, refresh=True)
    hourly = temperature(cache, frequency="hourly")
    assert hourly.metadata["source_generation"] == new.metadata["source_generation"]
    assert hourly.metadata["source_generation"] != old.metadata["source_generation"]
    assert temperature(cache).metadata["result_id"] == new.metadata["result_id"]


def test_concurrent_refresh_and_lookup_commit_consistency(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import meteorology.request_adapters as adapters

    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    temperature(cache)
    provider.temperature = 293.15
    entered, release = Event(), Event()
    original = adapters.era5_day

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(adapters, "era5_day", blocked)
    with ThreadPoolExecutor(2) as pool:
        writer = pool.submit(temperature, cache, refresh=True)
        assert entered.wait(10)
        reader = pool.submit(temperature, cache, dry_run=True)
        assert not reader.done()
        release.set()
        new, preview = writer.result(), reader.result()
    assert preview["cache"]["final_result_hit"]
    assert temperature(cache).metadata["result_id"] == new.metadata["result_id"]


def test_failed_active_index_write_preserves_prior_generation(tmp_path, monkeypatch):
    from meteorology.request_storage import RequestBudget

    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    old = temperature(cache)
    original = RequestBudget.write_json

    def failure(self, path, value):
        if path.name == "ACTIVE_GENERATIONS.json":
            raise OSError("fixture promotion failure")
        return original(self, path, value)

    provider.temperature = 293.15
    monkeypatch.setattr(RequestBudget, "write_json", failure)
    with pytest.raises(OSError, match="promotion failure"):
        temperature(cache, refresh=True)
    assert temperature(cache).metadata["result_id"] == old.metadata["result_id"]


def test_refreshed_vintage_cannot_acquire_a_new_exact_era5_subset_job(tmp_path, monkeypatch):
    from meteorology.request_cache import CacheError

    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    get_weather(
        ["temperature", "cloud_cover"],
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        cache_dir=cache,
        frequency="hourly",
        refresh=True,
    )
    before = len(provider.calls)
    with pytest.raises(CacheError, match="exact source selection"):
        temperature(cache)
    assert len(provider.calls) == before
