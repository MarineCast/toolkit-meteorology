from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest
import yaml

from meteorology.artifacts import (
    load_manifest,
    sha256_file,
    write_table,
)
from meteorology.config import (
    SurfaceWeatherConfig,
    load_meteorological_config,
)
from meteorology.daylight.build import build_daylight
from meteorology.daily_matrix import export as export_daily_matrix
from meteorology.daylight.inspect import inspect_daylight
from meteorology.lunar.build import build_lunar
from meteorology.lunar.inspect import inspect_lunar
from meteorology.modeling.feature_policy import apply_feature_policy, load_feature_policy
from meteorology.spatial_support.build import (
    build_meteorological_spatial_support,
    load_meteorological_support,
)
from meteorology.spatial_support.inspect import (
    inspect_meteorological_spatial_support,
)
from meteorology.surface_weather.build import (
    build_surface_weather,
)
from meteorology.surface_weather.download import (
    INVENTORY_SCHEMA,
    download_surface_weather,
    sample_path,
    snapshot_existing_surface_weather_acquisition,
)
from meteorology.surface_weather.inspect import (
    inspect_surface_weather,
)
from meteorology.surface_weather.sampling import (
    SAMPLE_SCHEMA,
    make_sample_times_for_local_date,
)
from meteorology.surface_weather.source import (
    RAW_COLUMNS,
    hrrr_aws_archive_uri,
    hrrr_logical_object_uri,
)
from meteorology.surface_weather.verify import (
    SHARED_LEGACY_FIELDS,
    _legacy_field_partial_evidence,
    verify_hrrr_r5_rebuild,
)
from meteorology.validation import validate_daily_matrix, validate_product


def _fixture_config(tmp_path: Path, *, end_date: str = "2024-01-02") -> Path:
    raw = yaml.safe_load(Path("config/data/environment_meteorological.yaml").read_text())
    raw_root = tmp_path / "raw"
    processed = tmp_path / "processed"
    raw["surface_weather"]["time"]["start_date"] = "2024-01-02"
    raw["surface_weather"]["time"]["end_date"] = end_date
    raw["surface_weather"]["raw"] = {
        "root_dir": str(raw_root),
        "working_inventory_path": str(raw_root / "HRRR_R5_WORKING_INVENTORY.parquet"),
        "inventory_path": str(raw_root / "HRRR_R5_SOURCE_INVENTORY.parquet"),
        "manifest_path": str(raw_root / "R5_DOWNLOAD_MANIFEST.json"),
    }
    raw["spatial_support"]["output"] = {
        "root_dir": str(processed / "spatial_support"),
        "manifest_path": str(processed / "spatial_support/MANIFEST.json"),
    }
    raw["surface_weather"]["output"] = {
        "daily_dir": str(processed / "surface_weather/H3_SURFACE_WEATHER_DAILY_RES_5"),
        "manifest_path": str(processed / "surface_weather/MANIFEST.json"),
    }
    raw["daylight"]["output"] = {
        "daily_dir": str(processed / "daylight/H3_DAYLIGHT_DAILY_RES_4"),
        "day_of_year_path": str(processed / "daylight/H3_DAYLIGHT_DOY_RES_4.parquet"),
        "manifest_path": str(processed / "daylight/MANIFEST.json"),
    }
    raw["lunar"]["output"] = {
        "daily_dir": str(processed / "lunar/H3_LUNAR_DAILY_RES_5"),
        "manifest_path": str(processed / "lunar/MANIFEST.json"),
    }
    path = tmp_path / "meteorological.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def _raw_grid(valid: pd.Timestamp, availability_lag_hours: int = 6) -> pd.DataFrame:
    available = valid + pd.Timedelta(hours=availability_lag_hours)
    rows = []
    for index, (lat, lon) in enumerate(
        [(46.5, -125.0), (46.5, -121.0), (50.5, -125.0), (50.5, -121.0)]
    ):
        rows.append(
            {
                "SOURCE_GRID_INDEX": index,
                "SOURCE_LAT": lat,
                "SOURCE_LON": lon,
                "VALID_TIME_UTC": valid.isoformat(),
                "INIT_TIME_UTC": valid.isoformat(),
                "AVAILABLE_AT_UTC": available.isoformat(),
                "SOURCE_MODEL": "hrrr",
                "SOURCE_PRODUCT": "sfc",
                "FORECAST_HOUR": 0,
                "SOURCE_GRID_HASH": "fixture-grid-v1",
                "SOURCE_WIND_BASIS": "earth_relative",
                "TEMPERATURE_2M_K": 283.15 + index,
                "RELATIVE_HUMIDITY_2M_PCT": 75.0,
                "U_WIND_10M_MS": 3.0,
                "V_WIND_10M_MS": 4.0,
                "WIND_GUST_SURFACE_MS": 12.0,
                "VISIBILITY_M": 15_000.0,
                "TOTAL_CLOUD_COVER_PCT": 50.0,
                "PRECIP_RATE_KG_M2_S": 0.0,
                "MEAN_SEA_LEVEL_PRESSURE_PA": 98_000.0,
            }
        )
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


def _patch_fetch(
    monkeypatch,
    calls: list[str],
    *,
    failed: set[str] | None = None,
    precip_rate: float = 0.001,
    availability_lag_hours: int = 6,
) -> None:
    from meteorology.surface_weather import download as module

    def fetch(**kwargs):
        valid = pd.Timestamp(kwargs["valid_time_utc"]).tz_convert("UTC")
        calls.append(valid.isoformat())
        if failed and valid.isoformat() in failed:
            raise RuntimeError("fixture acquisition failure")
        return _raw_grid(valid, availability_lag_hours), hrrr_aws_archive_uri(valid)

    def fetch_precip(**kwargs):
        valid = pd.Timestamp(kwargs["valid_time_utc"]).tz_convert("UTC")
        if failed and valid.isoformat() in failed:
            raise RuntimeError("fixture precipitation acquisition failure")
        core = _raw_grid(valid)
        frame = core[["SOURCE_GRID_INDEX", "SOURCE_LAT", "SOURCE_LON", "SOURCE_GRID_HASH"]].copy()
        frame["PRECIP_RATE_KG_M2_S"] = precip_rate
        frame["PRECIP_INIT_TIME_UTC"] = (valid - pd.Timedelta(hours=1)).isoformat()
        frame["PRECIP_VALID_TIME_UTC"] = valid.isoformat()
        frame["PRECIP_FORECAST_HOUR"] = 1
        return frame, hrrr_aws_archive_uri(valid, 1)

    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", fetch)
    monkeypatch.setattr(module, "fetch_cropped_hrrr_precip_grid", fetch_precip)


def _published_acquisition_bytes(config_path: Path) -> dict[Path, str]:
    config = load_meteorological_config(config_path)
    weather = config.surface_weather
    inventory = pq.read_table(weather.inventory_path).to_pandas()
    references = {
        weather.raw_dir / str(value)
        for field in ("RELATIVE_PATH", "CROSSWALK_RELATIVE_PATH")
        for value in inventory[field]
    }
    references.update((weather.inventory_path, weather.acquisition_manifest_path))
    return {path: sha256_file(path) for path in references}


def _assert_published_bytes(before: dict[Path, str]) -> None:
    assert {path: sha256_file(path) for path in before} == before


def test_complete_range_all_zero_forecast_precipitation_is_valid(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    _patch_fetch(monkeypatch, [], precip_rate=0.0)
    download_surface_weather(config_path, run_id="fixture-download")

    outputs = build_surface_weather(config_path, run_id="fixture-all-zero-weather")
    daily = ds.dataset(outputs[0], format="parquet", partitioning="hive").to_table().to_pandas()
    assert (daily["PRECIP_MM_DAY_ESTIMATE"] == 0.0).all()


def test_spatial_policy_migration_requires_full_overwrite(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls)
    download_surface_weather(config_path, run_id="fixture-download")
    weather = load_meteorological_config(config_path).surface_weather
    (weather.raw_dir / "SPATIAL_ACCEPTANCE_POLICY.json").unlink()
    calls.clear()
    with pytest.raises(ValueError, match="predate spatial acceptance"):
        download_surface_weather(config_path, run_id="fixture-reuse")
    assert calls == []
    download_surface_weather(config_path, run_id="fixture-migrated", overwrite=True)
    assert len(calls) == 6


def test_support_rejects_changed_bounding_box(tmp_path: Path) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    raw = yaml.safe_load(config_path.read_text())
    raw["surface_weather"]["time"]["end_date"] = "2024-01-03"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    assert not load_meteorological_support(5, config_path).empty
    changed_bbox = load_meteorological_config(config_path).bbox.copy()
    changed_bbox["max_lon"] += 0.1
    raw["spatial_support"]["bbox"] = {"bbox": changed_bbox}
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    with pytest.raises(ValueError, match="does not match the current bounding box"):
        load_meteorological_support(5, config_path)


def test_changed_availability_lag_invalidates_cached_samples(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls)
    download_surface_weather(config_path, run_id="first")
    raw = yaml.safe_load(config_path.read_text())
    raw["surface_weather"]["time"]["availability_lag_hours"] = 8
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    calls.clear()
    _patch_fetch(monkeypatch, calls, availability_lag_hours=8)
    download_surface_weather(config_path, run_id="changed-lag")
    assert len(calls) == 6
    config = load_meteorological_config(config_path)
    inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    for row in inventory.itertuples(index=False):
        sample = config.surface_weather.raw_dir / row.RELATIVE_PATH
        frame = pq.read_table(sample, columns=["AVAILABLE_AT_UTC"]).to_pandas()
        assert set(frame["AVAILABLE_AT_UTC"]) == {row.AVAILABLE_AT_UTC}


def test_incremental_lag_change_rejects_stale_retained_rows(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path, end_date="2024-01-03")
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(
        config_path, start_date="2024-01-02", end_date="2024-01-02", run_id="first"
    )
    config = load_meteorological_config(config_path)
    original = sha256_file(config.surface_weather.inventory_path)
    raw = yaml.safe_load(config_path.read_text())
    raw["surface_weather"]["time"]["availability_lag_hours"] = 8
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    _patch_fetch(monkeypatch, [], availability_lag_hours=8)
    with pytest.raises(ValueError, match="Retained HRRR row has incompatible release policy"):
        download_surface_weather(
            config_path, start_date="2024-01-03", end_date="2024-01-03", run_id="extension"
        )
    assert sha256_file(config.surface_weather.inventory_path) == original
    download_surface_weather(
        config_path, start_date="2024-01-02", end_date="2024-01-03", run_id="rebuilt"
    )
    inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert len(inventory) == 12
    assert all(
        pd.Timestamp(row.AVAILABLE_AT_UTC)
        == pd.Timestamp(row.VALID_TIME_UTC) + pd.Timedelta(hours=8)
        for row in inventory.itertuples(index=False)
    )


def test_failed_overlapping_lag_refresh_preserves_published_references(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _fixture_config(tmp_path, end_date="2024-01-03")
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="first")
    config = load_meteorological_config(config_path)
    original_config = config_path.read_bytes()
    inventory_path = config.surface_weather.inventory_path
    manifest_path = config.surface_weather.acquisition_manifest_path
    inventory = pq.read_table(inventory_path).to_pandas()
    references = {
        config.surface_weather.raw_dir / str(relative)
        for column in ("RELATIVE_PATH", "CROSSWALK_RELATIVE_PATH")
        for relative in inventory[column]
    }
    references.update((config.support_path(5), config.support_manifest_path))
    before = {
        path: sha256_file(path)
        for path in (inventory_path, manifest_path, *sorted(references))
    }

    raw = yaml.safe_load(config_path.read_text())
    raw["surface_weather"]["time"]["availability_lag_hours"] = 8
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    _patch_fetch(monkeypatch, [], availability_lag_hours=8)
    with pytest.raises(ValueError, match="incompatible release policy"):
        download_surface_weather(
            config_path, start_date="2024-01-03", end_date="2024-01-03", run_id="mixed"
        )
    config_path.write_bytes(original_config)

    changed = [str(path) for path, checksum in before.items()
               if not path.exists() or sha256_file(path) != checksum]
    assert changed == []
    assert validate_product(manifest_path)["valid"]
    assert build_surface_weather(config_path, run_id="old-still-usable")


def test_partial_overwrite_failure_preserves_published_references(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _fixture_config(tmp_path, end_date="2024-01-03")
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="first", max_workers=1)
    config = load_meteorological_config(config_path)
    inventory_path = config.surface_weather.inventory_path
    manifest_path = config.surface_weather.acquisition_manifest_path
    inventory = pq.read_table(inventory_path).to_pandas()
    references = {
        config.surface_weather.raw_dir / str(relative)
        for column in ("RELATIVE_PATH", "CROSSWALK_RELATIVE_PATH")
        for relative in inventory[column]
    }
    before = {path: sha256_file(path) for path in
              (inventory_path, manifest_path, *sorted(references))}
    late = make_sample_times_for_local_date(
        "2024-01-03", config.surface_weather.timezone, 4
    )[-1]
    from meteorology.surface_weather import download as module

    def changed_or_failed(**kwargs):
        valid = pd.Timestamp(kwargs["valid_time_utc"]).tz_convert("UTC")
        if valid == late:
            raise RuntimeError("fixture late provider failure")
        frame = _raw_grid(valid)
        frame["TEMPERATURE_2M_K"] += 2.0
        return frame, hrrr_aws_archive_uri(valid)

    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", changed_or_failed)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="canonical acquisition artifacts were not replaced"):
        download_surface_weather(
            config_path, start_date="2024-01-03", end_date="2024-01-03",
            overwrite=True, max_workers=1, run_id="partial",
        )
    changed = [str(path) for path, checksum in before.items()
               if not path.exists() or sha256_file(path) != checksum]
    assert changed == []
    assert build_surface_weather(config_path, run_id="old-still-usable")


def test_partial_refresh_resumes_retained_candidates(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="first", max_workers=1)
    before = _published_acquisition_bytes(config_path)
    from meteorology.surface_weather import download as module

    first_fetch = module.fetch_cropped_hrrr_grid
    late = make_sample_times_for_local_date("2024-01-02", "America/Los_Angeles", 4)[-1]

    def changed_then_failed(**kwargs):
        valid = pd.Timestamp(kwargs["valid_time_utc"]).tz_convert("UTC")
        if valid == late:
            raise RuntimeError("late fixture failure")
        frame, uri = first_fetch(**kwargs)
        frame["TEMPERATURE_2M_K"] += 2.0
        return frame, uri

    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", changed_then_failed)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="incomplete"):
        download_surface_weather(config_path, overwrite=True, max_workers=1)
    _assert_published_bytes(before)
    config = load_meteorological_config(config_path)
    working = pq.read_table(config.surface_weather.working_inventory_path).to_pandas()
    assert (working["STATUS"] == "COMPLETE").sum() == 5
    candidate_paths = set(working.loc[working["STATUS"] == "COMPLETE", "RELATIVE_PATH"])
    assert candidate_paths - set(pq.read_table(config.surface_weather.inventory_path).to_pandas()["RELATIVE_PATH"])
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls)
    result = download_surface_weather(config_path, max_workers=1, run_id="resumed")
    assert result["complete_times"] == 6
    assert calls == [pd.Timestamp(late).tz_convert("UTC").isoformat()]
    assert validate_product(config.surface_weather.acquisition_manifest_path)["valid"]
    _assert_published_bytes({path: checksum for path, checksum in before.items()
                             if path not in {config.surface_weather.inventory_path,
                                             config.surface_weather.acquisition_manifest_path}})


def test_prepublication_corrupt_candidate_and_cancellation_leave_old_release(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="first", max_workers=1)
    before = _published_acquisition_bytes(config_path)
    from meteorology.surface_weather import download as module

    source_fetch = module.fetch_cropped_hrrr_grid

    def changed(**kwargs):
        frame, uri = source_fetch(**kwargs)
        frame["TEMPERATURE_2M_K"] += 3.0
        return frame, uri

    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", changed)
    real_write = module.write_immutable_table
    corrupted = False

    def corrupt_candidate(*args, **kwargs):
        nonlocal corrupted
        path, checksum = real_write(*args, **kwargs)
        if kwargs["family"] == "samples" and not corrupted:
            path.write_bytes(path.read_bytes() + b"corrupt")
            corrupted = True
        return path, checksum

    monkeypatch.setattr(module, "write_immutable_table", corrupt_candidate)
    with pytest.raises(ValueError, match="sample or crosswalk is invalid"):
        download_surface_weather(config_path, overwrite=True, max_workers=1)
    _assert_published_bytes(before)
    assert validate_product(load_meteorological_config(config_path).surface_weather.acquisition_manifest_path)["valid"]

    monkeypatch.setattr(module, "write_immutable_table", real_write)
    _patch_fetch(monkeypatch, [])
    real_fetch = module.fetch_cropped_hrrr_grid
    count = 0

    def cancel_after_one(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise KeyboardInterrupt("fixture cancellation")
        return real_fetch(**kwargs)

    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", cancel_after_one)
    with pytest.raises(KeyboardInterrupt, match="fixture cancellation"):
        download_surface_weather(config_path, overwrite=True, max_workers=1)
    _assert_published_bytes(before)
    states = load_meteorological_config(config_path).surface_weather.raw_dir.glob("runs/*/RUN_STATE.json")
    assert any(json.loads(path.read_text())["status"] == "INCOMPLETE" for path in states)
    _patch_fetch(monkeypatch, [])
    assert download_surface_weather(config_path, max_workers=1)["complete_times"] == 6


@pytest.mark.parametrize("phase,exit_code", [("candidate", 71), ("promoting", 72), ("committed", 73)])
def test_hard_interruption_recovers_complete_generation(
    tmp_path: Path, monkeypatch, phase: str, exit_code: int
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="first", max_workers=1)
    before = _published_acquisition_bytes(config_path)
    config = load_meteorological_config(config_path)
    child = Path(__file__).with_name("test_acquisition_interrupt_child.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, str(child), str(config_path), phase],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == exit_code, result.stderr
    if phase in {"promoting", "committed"}:
        with pytest.raises(RuntimeError, match="requires recovery"):
            validate_product(config.surface_weather.acquisition_manifest_path)
    from meteorology.core.artifacts import TransactionalFamilyPublisher
    from meteorology.surface_weather.storage import acquisition_lock

    with acquisition_lock(config.surface_weather.raw_dir, writer=True):
        TransactionalFamilyPublisher.recover(config.surface_weather.raw_dir)
    assert validate_product(config.surface_weather.acquisition_manifest_path)["valid"]
    if phase != "committed":
        _assert_published_bytes(before)
    else:
        assert sha256_file(config.surface_weather.acquisition_manifest_path) != before[
            config.surface_weather.acquisition_manifest_path
        ]
        _assert_published_bytes({path: checksum for path, checksum in before.items()
                                 if path not in {config.surface_weather.inventory_path,
                                                 config.surface_weather.acquisition_manifest_path}})
    if phase == "candidate":
        assert list((config.surface_weather.raw_dir / "objects" / "samples").glob("*.parquet"))
        interrupted = [path for path in config.surface_weather.raw_dir.glob("runs/*/RUN_STATE.json")
                       if json.loads(path.read_text())["status"] == "ACQUIRING"]
        assert len(interrupted) == 1
        working = pq.read_table(interrupted[0].parent / "WORKING_INVENTORY.parquet").to_pandas()
        assert len(working) == 6 and set(working["STATUS"]) == {"COMPLETE"}
    _patch_fetch(monkeypatch, [])
    assert download_surface_weather(config_path, max_workers=1)["complete_times"] == 6


def test_concurrent_writer_and_reader_get_explicit_busy(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="first", max_workers=1)
    before = _published_acquisition_bytes(config_path)
    from meteorology.surface_weather import download as module

    entered, release = threading.Event(), threading.Event()
    normal_fetch = module.fetch_cropped_hrrr_grid

    def blocked_fetch(**kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return normal_fetch(**kwargs)

    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", blocked_fetch)
    errors: list[BaseException] = []

    def first_writer():
        try:
            download_surface_weather(config_path, overwrite=True, max_workers=1)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=first_writer)
    thread.start()
    assert entered.wait(timeout=10)
    try:
        with pytest.raises(RuntimeError, match="workspace is busy"):
            download_surface_weather(config_path, overwrite=True, max_workers=1)
        with pytest.raises(RuntimeError, match="workspace is busy"):
            validate_product(load_meteorological_config(config_path).surface_weather.acquisition_manifest_path)
    finally:
        release.set()
        thread.join(timeout=10)
    assert not thread.is_alive() and errors == []
    _assert_published_bytes({path: checksum for path, checksum in before.items()
                             if path not in {load_meteorological_config(config_path).surface_weather.inventory_path,
                                             load_meteorological_config(config_path).surface_weather.acquisition_manifest_path}})


def test_full_refresh_keeps_old_objects_and_frozen_releases_relocatable(
    tmp_path: Path, monkeypatch
) -> None:
    from meteorology.releases import freeze_release

    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="first", max_workers=1)
    build_surface_weather(config_path, run_id="first-weather")
    config = load_meteorological_config(config_path)
    old = _published_acquisition_bytes(config_path)
    old_inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    frozen_acquisition = freeze_release(config.surface_weather.acquisition_manifest_path, tmp_path / "releases")
    frozen_weather = freeze_release(config.surface_weather.manifest_path, tmp_path / "releases")
    from meteorology.surface_weather import download as module

    real_fetch = module.fetch_cropped_hrrr_grid

    def warmer(**kwargs):
        frame, uri = real_fetch(**kwargs)
        frame["TEMPERATURE_2M_K"] += 2.0
        return frame, uri

    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", warmer)
    download_surface_weather(config_path, overwrite=True, max_workers=1, run_id="warmer")
    new_inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert set(old_inventory["RELATIVE_PATH"]).isdisjoint(set(new_inventory["RELATIVE_PATH"]))
    _assert_published_bytes({path: checksum for path, checksum in old.items()
                             if path not in {config.surface_weather.inventory_path,
                                             config.surface_weather.acquisition_manifest_path}})
    assert validate_product(config.surface_weather.acquisition_manifest_path)["valid"]
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    shutil.move(str(frozen_acquisition.parent), str(relocated / "acquisition"))
    shutil.move(str(frozen_weather.parent), str(relocated / "weather"))
    hidden_raw = config.surface_weather.raw_dir.with_name("raw-hidden")
    config.surface_weather.raw_dir.rename(hidden_raw)
    try:
        assert validate_product(relocated / "acquisition" / "MANIFEST.json")["valid"]
        assert validate_product(relocated / "weather" / "MANIFEST.json")["valid"]
    finally:
        hidden_raw.rename(config.surface_weather.raw_dir)


def test_weather_build_pins_consumed_acquisition_generation_during_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    from importlib import import_module
    from meteorology.releases import freeze_release

    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="generation-a", max_workers=1)
    config = load_meteorological_config(config_path)
    old_inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    old_references = set(old_inventory["RELATIVE_PATH"])
    build_module = import_module("meteorology.surface_weather.build")
    download_module = import_module("meteorology.surface_weather.download")
    original_reader = build_module._read_validated_sample
    original_fetch = download_module.fetch_cropped_hrrr_grid
    refreshed = False

    def refresh_before_first_sample(**kwargs):
        nonlocal refreshed
        if not refreshed:
            refreshed = True

            def warmer(**fetch_kwargs):
                frame, uri = original_fetch(**fetch_kwargs)
                frame["TEMPERATURE_2M_K"] += 2.0
                return frame, uri

            monkeypatch.setattr(download_module, "fetch_cropped_hrrr_grid", warmer)
            download_surface_weather(
                config_path, run_id="generation-b", overwrite=True, max_workers=1
            )
        return original_reader(**kwargs)

    monkeypatch.setattr(build_module, "_read_validated_sample", refresh_before_first_sample)
    weather_manifest = build_surface_weather(config_path, run_id="consumed-a")[-1]
    assert refreshed
    new_inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert old_references.isdisjoint(set(new_inventory["RELATIVE_PATH"]))
    payload = load_manifest(weather_manifest, verify_artifacts=True)
    pinned_inventory = Path(payload["inputs"][0]["path"])
    assert pinned_inventory != config.surface_weather.inventory_path
    assert set(pq.read_table(pinned_inventory).to_pandas()["RELATIVE_PATH"]) == old_references
    build_meteorological_spatial_support(config_path, run_id="support-b")
    assert Path(payload["inputs"][3]["path"]) != config.support_manifest_path
    load_manifest(Path(payload["inputs"][1]["path"]), verify_artifacts=True)
    assert validate_product(weather_manifest)["valid"]
    frozen = freeze_release(weather_manifest, tmp_path / "releases")
    relocated = tmp_path / "relocated-weather"
    shutil.move(frozen.parent, relocated)
    hidden_raw = config.surface_weather.raw_dir.with_name("raw-hidden")
    config.surface_weather.raw_dir.rename(hidden_raw)
    try:
        assert validate_product(relocated / "MANIFEST.json")["valid"]
    finally:
        hidden_raw.rename(config.surface_weather.raw_dir)


@pytest.mark.parametrize("pressure_pa", [75_000.0, 115_000.0])
def test_pressure_hard_contract_agrees_through_build_validate_and_freeze(
    tmp_path: Path, monkeypatch, pressure_pa: float
) -> None:
    from meteorology.releases import freeze_release
    from meteorology.field_contracts import regional_warning_counts
    from meteorology.surface_weather import download as download_module

    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    original_fetch = download_module.fetch_cropped_hrrr_grid

    def unusual_pressure(**kwargs):
        frame, uri = original_fetch(**kwargs)
        frame["MEAN_SEA_LEVEL_PRESSURE_PA"] = pressure_pa
        return frame, uri

    monkeypatch.setattr(download_module, "fetch_cropped_hrrr_grid", unusual_pressure)
    download_surface_weather(config_path, run_id="unusual-pressure", max_workers=1)
    manifest = build_surface_weather(config_path, run_id="unusual-pressure-weather")[-1]
    assert validate_product(manifest)["valid"]
    config = load_meteorological_config(config_path)
    daily = ds.dataset(config.surface_weather.daily_output_dir, format="parquet", partitioning="hive").to_table().to_pandas()
    assert set(daily["MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN"]) == {pressure_pa / 100.0}
    assert regional_warning_counts(daily)["MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN"] == len(daily)
    assert validate_product(manifest)["regional_warning_counts"]["MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN"] == len(daily)
    assert validate_product(freeze_release(manifest, tmp_path / "releases"))["valid"]


def test_matrix_and_freeze_pin_product_generations_during_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    from importlib import import_module
    from meteorology.releases import freeze_release

    config_path = _fixture_config(tmp_path)
    raw_config = yaml.safe_load(config_path.read_text())
    raw_config["surface_weather"]["output"]["manifest_path"] = str(
        tmp_path / "custom-manifests/weather.json"
    )
    config_path.write_text(yaml.safe_dump(raw_config, sort_keys=False))
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="acquisition-a", max_workers=1)
    weather_manifest = build_surface_weather(config_path, run_id="weather-a")[-1]
    daylight_manifest = build_daylight(config_path, run_id="daylight")[-1]
    lunar_manifest = build_lunar(config_path, run_id="lunar")[-1]
    config = load_meteorological_config(config_path)
    original_weather = ds.dataset(
        config.surface_weather.daily_output_dir, format="parquet", partitioning="hive"
    ).to_table().to_pandas()
    matrix_module = import_module("meteorology.daily_matrix")
    download_module = import_module("meteorology.surface_weather.download")
    original_combine = matrix_module.combine_frames
    original_fetch = download_module.fetch_cropped_hrrr_grid
    refreshed = False

    def refresh_during_export(frames):
        nonlocal refreshed
        if not refreshed:
            refreshed = True

            def warmer(**kwargs):
                frame, uri = original_fetch(**kwargs)
                frame["TEMPERATURE_2M_K"] += 2.0
                return frame, uri

            monkeypatch.setattr(download_module, "fetch_cropped_hrrr_grid", warmer)
            download_surface_weather(config_path, overwrite=True, run_id="acquisition-b", max_workers=1)
        return original_combine(frames)

    monkeypatch.setattr(matrix_module, "combine_frames", refresh_during_export)
    matrix_path = export_daily_matrix(
        [weather_manifest, daylight_manifest, lunar_manifest], tmp_path / "matrix.parquet"
    )
    assert refreshed and validate_daily_matrix(matrix_path)["valid"]
    matrix = pq.read_table(matrix_path).to_pandas()
    weather_rows = matrix[matrix["H3_RESOLUTION"] == 5].sort_values("H3_INDEX")
    assert weather_rows["surface_weather__TEMPERATURE_2M_C_MEAN"].tolist() == (
        original_weather.sort_values("H3_INDEX")["TEMPERATURE_2M_C_MEAN"].tolist()
    )

    releases_module = import_module("meteorology.releases")
    original_copy = releases_module._copy_verified
    tried_rebuild = False

    def rebuild_during_freeze(source, destination, checksum):
        nonlocal tried_rebuild
        if not tried_rebuild:
            tried_rebuild = True
            with pytest.raises(RuntimeError, match="Publication parent is busy"):
                build_surface_weather(config_path, run_id="weather-b")
        return original_copy(source, destination, checksum)

    monkeypatch.setattr(releases_module, "_copy_verified", rebuild_during_freeze)
    frozen = freeze_release(weather_manifest, tmp_path / "releases")
    assert tried_rebuild and validate_product(frozen)["valid"]


def test_interrupted_metadata_snapshot_does_not_publish_weather(
    tmp_path: Path, monkeypatch
) -> None:
    from importlib import import_module

    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="acquisition", max_workers=1)
    config = load_meteorological_config(config_path)
    storage = import_module("meteorology.surface_weather.storage")
    original_copy = storage.shutil.copy2

    def interrupted_copy(*_args, **_kwargs):
        raise RuntimeError("injected snapshot interruption")

    monkeypatch.setattr(storage.shutil, "copy2", interrupted_copy)
    with pytest.raises(RuntimeError, match="injected snapshot interruption"):
        build_surface_weather(config_path, run_id="interrupted")
    assert not config.surface_weather.manifest_path.exists()
    assert not list((config.surface_weather.raw_dir / "support_snapshots").glob(".*.part"))
    monkeypatch.setattr(storage.shutil, "copy2", original_copy)
    assert validate_product(build_surface_weather(config_path, run_id="recovered")[-1])["valid"]


def test_disjoint_acquisitions_do_not_claim_a_complete_interval(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path, end_date="2024-01-04")
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(
        config_path, start_date="2024-01-02", end_date="2024-01-02", run_id="first"
    )
    config = load_meteorological_config(config_path)
    original = sha256_file(config.surface_weather.inventory_path)
    with pytest.raises(ValueError, match="continuous declared interval"):
        download_surface_weather(
            config_path, start_date="2024-01-04", end_date="2024-01-04", run_id="gap"
        )
    assert sha256_file(config.surface_weather.inventory_path) == original
    working = pq.read_table(config.surface_weather.working_inventory_path).to_pandas()
    assert len(working) == 12
    download_surface_weather(
        config_path, start_date="2024-01-03", end_date="2024-01-03", run_id="filled"
    )
    assert len(pq.read_table(config.surface_weather.inventory_path)) == 18


def test_unrelated_failed_row_blocks_later_canonical_publication(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path, end_date="2024-01-03")
    build_meteorological_spatial_support(config_path, run_id="support")
    config = load_meteorological_config(config_path)
    failed_time = make_sample_times_for_local_date(
        "2024-01-02", config.surface_weather.timezone, 4
    )[0].isoformat()
    _patch_fetch(monkeypatch, [], failed={failed_time})
    with pytest.raises(RuntimeError, match="incomplete"):
        download_surface_weather(
            config_path, start_date="2024-01-02", end_date="2024-01-02", run_id="failed"
        )
    _patch_fetch(monkeypatch, [])
    with pytest.raises(RuntimeError, match="canonical acquisition artifacts were not replaced"):
        download_surface_weather(
            config_path, start_date="2024-01-03", end_date="2024-01-03", run_id="later"
        )
    assert not config.surface_weather.inventory_path.exists()
    assert not config.surface_weather.acquisition_manifest_path.exists()
    working = pq.read_table(config.surface_weather.working_inventory_path).to_pandas()
    assert working["STATUS"].value_counts().to_dict() == {"COMPLETE": 11, "FAILED": 1}
    download_surface_weather(
        config_path, start_date="2024-01-02", end_date="2024-01-03", run_id="recovered"
    )
    assert len(pq.read_table(config.surface_weather.inventory_path)) == 12


def test_mirror_uri_is_retained_separately_from_logical_hrrr_object(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls)
    from meteorology.surface_weather import download as module

    fetch_core = module.fetch_cropped_hrrr_grid
    fetch_precip = module.fetch_cropped_hrrr_precip_grid

    def mirror_core(**kwargs):
        frame, uri = fetch_core(**kwargs)
        return frame, uri.replace("noaa-hrrr-bdp-pds.s3.amazonaws.com", "mirror.example.org")

    def mirror_precip(**kwargs):
        frame, uri = fetch_precip(**kwargs)
        return frame, uri.replace("noaa-hrrr-bdp-pds.s3.amazonaws.com", "mirror.example.org")

    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", mirror_core)
    monkeypatch.setattr(module, "fetch_cropped_hrrr_precip_grid", mirror_precip)
    download_surface_weather(config_path, run_id="mirror")
    config = load_meteorological_config(config_path)
    inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert inventory["SOURCE_URI"].str.startswith("https://mirror.example.org/").all()
    assert inventory["SOURCE_OBJECT_URI"].str.startswith("noaa-hrrr://archive/hrrr.").all()
    assert all(
        row.SOURCE_OBJECT_URI == hrrr_logical_object_uri(pd.Timestamp(row.VALID_TIME_UTC))
        for row in inventory.itertuples(index=False)
    )
    assert inventory["SOURCE_RETRIEVED_AT_UTC"].notna().all()
    assert inventory["PRECIP_RETRIEVED_AT_UTC"].notna().all()
    assert len(calls) == 6
    download_surface_weather(config_path, run_id="mirror-cache")
    assert len(calls) == 6
    resumed = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert resumed["SOURCE_URI"].equals(inventory["SOURCE_URI"])


def test_matrix_rejects_same_count_changed_h3_membership(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path, end_date="2024-01-03")
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, run_id="download")
    build_surface_weather(config_path, run_id="weather")
    build_daylight(config_path, start_date="2024-01-02", end_date="2024-01-03", run_id="daylight")
    build_lunar(config_path, start_date="2024-01-02", end_date="2024-01-03", run_id="lunar")
    config = load_meteorological_config(config_path)
    matrix_path = tmp_path / "matrix.parquet"
    export_daily_matrix(
        [config.surface_weather.manifest_path, config.daylight.manifest_path, config.lunar.manifest_path],
        matrix_path,
    )
    assert validate_daily_matrix(matrix_path)["valid"]
    selected = apply_feature_policy(pq.read_table(matrix_path).to_pandas(), load_feature_policy())
    assert list(selected.columns[:3]) == ["DATE", "H3_INDEX", "H3_RESOLUTION"]
    assert set(selected["H3_RESOLUTION"]) == {4, 5}
    assert selected.loc[selected["H3_RESOLUTION"] == 4, "surface_weather__PRECIP_MM_DAY_ESTIMATE"].isna().all()
    assert selected.loc[selected["H3_RESOLUTION"] == 5, "daylight__DAYLIGHT_HOURS"].isna().all()
    table = pq.read_table(matrix_path)
    dates = table["DATE"].to_pylist()
    resolutions = table["H3_RESOLUTION"].to_pylist()
    cells = table["H3_INDEX"].to_pylist()
    index = next(i for i, (date, resolution) in enumerate(zip(dates, resolutions, strict=True)) if date == "2024-01-03" and resolution == 5)
    cells[index] = h3.latlng_to_cell(0.0, 0.0, 5)
    changed = table.set_column(
        table.schema.get_field_index("H3_INDEX"), "H3_INDEX", pa.array(cells, type=pa.string())
    )
    pq.write_table(changed, matrix_path)
    sidecar = matrix_path.with_suffix(".parquet.manifest.json")
    content = json.loads(sidecar.read_text())
    sidecar.write_text(json.dumps({**content, "matrix_checksum": sha256_file(matrix_path)}))
    with pytest.raises(ValueError, match="support membership varies"):
        validate_daily_matrix(matrix_path)


def test_direct_r5_download_build_and_inspect_contract(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    support = load_meteorological_support(5, config_path)
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls)

    summary = download_surface_weather(config_path, run_id="fixture-download")
    assert summary["complete_times"] == 6
    assert len(calls) == 6
    config = load_meteorological_config(config_path)
    inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert set(inventory["STATUS"]) == {"COMPLETE"}
    assert set(inventory["SOURCE_BACKEND"]) == {"grib"}
    assert set(inventory["H3_RESOLUTION"]) == {5}
    assert set(inventory["H3_CELL_COUNT"]) == {len(support)}
    assert set(inventory["VARIABLE_COUNT"]) == {9}
    assert set(inventory["PRECIP_FORECAST_HOUR"]) == {1}
    assert all(
        row.PRECIP_SOURCE_URI == hrrr_aws_archive_uri(pd.Timestamp(row.VALID_TIME_UTC), 1)
        for row in inventory.itertuples(index=False)
    )
    crosswalks = set(inventory["CROSSWALK_RELATIVE_PATH"].astype(str))
    assert len(crosswalks) == 1
    assert all(value.startswith("objects/crosswalks/") for value in crosswalks)
    load_manifest(config.surface_weather.acquisition_manifest_path, verify_artifacts=True)
    from meteorology.validation import validate_product

    assert validate_product(config.surface_weather.acquisition_manifest_path)["valid"]

    config.surface_weather.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    config.surface_weather.manifest_path.write_text(
        '{"product": "legacy-r6", "h3_resolution": 6}\n', encoding="utf-8"
    )
    outputs = build_surface_weather(config_path, run_id="fixture-weather")
    legacy_manifest = (
        config.surface_weather.manifest_path.parent / "PRE_R5_SURFACE_WEATHER_MANIFEST.json"
    )
    assert json.loads(legacy_manifest.read_text())["h3_resolution"] == 6
    daily = ds.dataset(outputs[0], format="parquet", partitioning="hive").to_table().to_pandas()
    assert len(daily) == len(support)
    assert set(daily["H3_INDEX"]) == set(support["H3_INDEX"])
    assert set(daily["QC_STATE"]) == {"COMPLETE"}
    assert set(daily["EXPECTED_SAMPLE_COUNT"]) == {6}
    assert set(daily["SAMPLE_COUNT"]) == {6}
    assert np.allclose(daily["PRECIP_MM_DAY_ESTIMATE"], 86.4)
    assert not daily.isna().any().any()
    load_manifest(outputs[-1], verify_artifacts=True)
    checked = validate_product(outputs[-1])
    assert checked["valid"] and checked["row_count"] == 2 * len(support)
    from meteorology.releases import freeze_release

    frozen = freeze_release(outputs[-1], tmp_path / "frozen-releases")
    assert frozen.parent.name == checked["release_id"]
    assert validate_product(frozen)["valid"]
    with pytest.raises(FileExistsError, match="already exists"):
        freeze_release(outputs[-1], tmp_path / "frozen-releases")
    relocated = tmp_path / "relocated-release"
    shutil.move(frozen.parent, relocated)
    relocated_manifest = relocated / "MANIFEST.json"
    assert validate_product(relocated_manifest)["valid"]
    archived = json.loads(relocated_manifest.read_text())
    assert all(not Path(item["path"]).is_absolute() for item in archived["artifacts"])
    assert all(not Path(item["path"]).is_absolute() for item in archived["inputs"])
    archived_inventory = relocated / "inputs/hrrr_samples/HRRR_R5_SOURCE_INVENTORY.parquet"
    assert archived_inventory.is_file()
    archived_rows = pq.read_table(archived_inventory).to_pandas()
    assert all((archived_inventory.parent / relative).exists() for relative in archived_rows["RELATIVE_PATH"])

    legacy = daily[["H3_INDEX", "DATE", *SHARED_LEGACY_FIELDS]].rename(
        columns={
            "H3_INDEX": "h3",
            "DATE": "date",
            **{current: former for current, former in SHARED_LEGACY_FIELDS.items()},
        }
    )
    legacy.insert(2, "n_obs", 6)
    legacy_path = tmp_path / "legacy/year=2024/date=2024-01-02/part-000.parquet"
    legacy_path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pandas(legacy, preserve_index=False), legacy_path)
    verification = verify_hrrr_r5_rebuild(
        config_path,
        legacy_root=tmp_path / "legacy",
        output_path=tmp_path / "R5_REBUILD_VERIFICATION.json",
    )
    assert verification["passed"]
    assert verification["legacy_comparison"]["compared_dates"] == 1
    assert verification["legacy_comparison"]["comparison_status"] == "complete"
    assert verification["pressure_validation"]["minimum_hpa"] == 980.0

    without_legacy = verify_hrrr_r5_rebuild(
        config_path,
        legacy_root=tmp_path / "missing-legacy",
        output_path=tmp_path / "R5_REBUILD_VERIFICATION_WITHOUT_LEGACY.json",
    )
    assert without_legacy["passed"]
    assert without_legacy["legacy_comparison"]["comparison_status"] == "unavailable_legacy_root"

    sample_files = [
        config.surface_weather.raw_dir / relative for relative in inventory["RELATIVE_PATH"]
    ]
    field_partial = legacy.copy()
    field_partial["relative_humidity_2m_pct_mean"] *= 5.0 / 6.0
    evidence = _legacy_field_partial_evidence(
        old=field_partial, sample_files=sample_files, interval_hours=4
    )
    assert evidence is not None
    assert evidence["relative_humidity"]["observed_sample_count"] == 5

    selector_ambiguous = legacy.copy()
    selector_ambiguous.loc[selector_ambiguous.index[0], "total_cloud_cover_pct_mean"] += 7.0
    evidence = _legacy_field_partial_evidence(
        old=selector_ambiguous, sample_files=sample_files, interval_hours=4
    )
    assert evidence is not None
    assert evidence["cloud_cover"]["diagnosis"] == "UNEXPLAINED_CLOUD_MISMATCH"
    pq.write_table(pa.Table.from_pandas(selector_ambiguous, preserve_index=False), legacy_path)
    unexplained = verify_hrrr_r5_rebuild(
        config_path,
        legacy_root=tmp_path / "legacy",
        output_path=tmp_path / "R5_REBUILD_VERIFICATION_UNEXPLAINED.json",
    )
    assert not unexplained["passed"]
    assert unexplained["legacy_comparison"]["unexplained_cloud_dates"] == 1
    assert unexplained["legacy_comparison"]["compared_dates"] == 1
    pq.write_table(pa.Table.from_pandas(legacy, preserve_index=False), legacy_path)

    build_daylight(
        config_path, start_date="2024-01-02", end_date="2024-01-02", run_id="fixture-daylight"
    )
    build_lunar(config_path, start_date="2024-01-02", end_date="2024-01-02", run_id="fixture-lunar")
    matrix_path = tmp_path / "DAILY_MATRIX.parquet"
    export_daily_matrix(
        [config.surface_weather.manifest_path, config.daylight.manifest_path, config.lunar.manifest_path],
        matrix_path,
    )
    assert validate_daily_matrix(matrix_path)["valid"]
    sidecar = matrix_path.with_suffix(".parquet.manifest.json")
    content = json.loads(sidecar.read_text())
    sidecar.write_text(json.dumps({**content, "matrix_checksum": "wrong"}))
    with pytest.raises(ValueError, match="content checksum"):
        validate_daily_matrix(matrix_path)
    original_table = pq.read_table(matrix_path)
    column = "surface_weather__PRECIP_MM_DAY_ESTIMATE"
    values = original_table[column].to_pylist()
    values[next(i for i, value in enumerate(values) if value is not None)] = -1.0
    table = original_table.set_column(
        original_table.schema.get_field_index(column), column, pa.array(values, type=pa.float64())
    )
    pq.write_table(table, matrix_path)
    sidecar.write_text(json.dumps({**content, "matrix_checksum": sha256_file(matrix_path)}))
    with pytest.raises(ValueError, match="outside its declared range"):
        validate_daily_matrix(matrix_path)
    missing_column = original_table.drop(["surface_weather__TEMPERATURE_2M_C_MEAN"])
    pq.write_table(missing_column, matrix_path)
    sidecar.write_text(json.dumps({**content, "matrix_checksum": sha256_file(matrix_path)}))
    with pytest.raises(ValueError, match="scientific Arrow schema"):
        validate_daily_matrix(matrix_path)
    column = "surface_weather__TEMPERATURE_2M_C_MEAN"
    values = original_table[column].to_pylist()
    values[next(i for i, value in enumerate(values) if value is not None)] = None
    native_null = original_table.set_column(
        original_table.schema.get_field_index(column), column, pa.array(values, type=pa.float64())
    )
    pq.write_table(native_null, matrix_path)
    sidecar.write_text(json.dumps({**content, "matrix_checksum": sha256_file(matrix_path)}))
    with pytest.raises(ValueError, match="non-nullable fields"):
        validate_daily_matrix(matrix_path)
    for inspect in (
        lambda: inspect_meteorological_spatial_support(
            config_path, output_path=tmp_path / "support.html"
        ),
        lambda: inspect_surface_weather(
            config_path, date="2024-01-02", output_path=tmp_path / "weather.html"
        ),
        lambda: inspect_daylight(
            config_path, date="2024-01-02", output_path=tmp_path / "daylight.html"
        ),
        lambda: inspect_lunar(config_path, date="2024-01-02", output_path=tmp_path / "lunar.html"),
    ):
        assert inspect().exists()


@pytest.mark.parametrize("field,placeholder", [
    ("SOURCE_URI", "aws"),
    ("SOURCE_URI", "existing_validated_local_sample"),
    ("SOURCE_RETRIEVED_AT_UTC", "not-a-date"),
    ("PRECIP_RETRIEVED_AT_UTC", "2024-01-01T00:00:00"),
])
def test_download_resumes_and_repairs_checksum_mismatch(
    tmp_path: Path, monkeypatch, field: str, placeholder: str
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls)
    download_surface_weather(config_path, run_id="download-first")
    config = load_meteorological_config(config_path)
    inventory = pq.read_table(config.surface_weather.working_inventory_path).to_pandas()
    inventory[field] = placeholder
    write_table(
        config.surface_weather.working_inventory_path,
        pa.Table.from_pandas(inventory, preserve_index=False),
        INVENTORY_SCHEMA,
    )
    download_surface_weather(config_path, run_id="download-resume")
    assert len(calls) == (6 if field == "PRECIP_RETRIEVED_AT_UTC" else 12)
    resumed = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert resumed["SOURCE_URI"].str.startswith("https://noaa-hrrr-bdp-pds.s3.amazonaws.com/").all()
    assert placeholder not in set(resumed[field].astype(str))

    first_time = make_sample_times_for_local_date("2024-01-02", config.surface_weather.timezone, 4)[
        0
    ]
    first_row = resumed.loc[
        resumed["VALID_TIME_UTC"] == pd.Timestamp(first_time).tz_convert("UTC").isoformat()
    ].iloc[0]
    path = config.surface_weather.raw_dir / str(first_row["RELATIVE_PATH"])
    legacy_sample = sample_path(config.surface_weather.raw_dir, first_time, config.surface_weather.timezone)
    legacy_sample.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, legacy_sample)
    candidate = resumed.copy()
    candidate.loc[candidate["VALID_TIME_UTC"] == first_row["VALID_TIME_UTC"], "RELATIVE_PATH"] = str(
        legacy_sample.relative_to(config.surface_weather.raw_dir)
    )
    write_table(config.surface_weather.working_inventory_path,
                pa.Table.from_pandas(candidate, preserve_index=False), INVENTORY_SCHEMA)
    tampered = pq.ParquetFile(legacy_sample).read().to_pandas()
    tampered["TEMPERATURE_2M_C"] += 1.0
    write_table(legacy_sample, pa.Table.from_pandas(tampered, preserve_index=False), SAMPLE_SCHEMA)
    download_surface_weather(config_path, run_id="download-repair")
    assert len(calls) == (7 if field == "PRECIP_RETRIEVED_AT_UTC" else 13)

    inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    original_crosswalk = config.surface_weather.raw_dir / inventory["CROSSWALK_RELATIVE_PATH"].iloc[0]
    crosswalk = config.surface_weather.raw_dir / "crosswalks" / "legacy-working.parquet"
    crosswalk.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original_crosswalk, crosswalk)
    inventory["CROSSWALK_RELATIVE_PATH"] = str(crosswalk.relative_to(config.surface_weather.raw_dir))
    write_table(config.surface_weather.working_inventory_path,
                pa.Table.from_pandas(inventory, preserve_index=False), INVENTORY_SCHEMA)
    crosswalk_frame = pq.ParquetFile(crosswalk).read().to_pandas()
    crosswalk_frame["SOURCE_GRID_DISTANCE_M"] += 1.0
    from meteorology.surface_weather.sampling import (
        CROSSWALK_SCHEMA,
    )

    write_table(
        crosswalk,
        pa.Table.from_pandas(crosswalk_frame, preserve_index=False),
        CROSSWALK_SCHEMA,
    )
    calls_before_crosswalk_repair = len(calls)
    download_surface_weather(config_path, run_id="crosswalk-repair")
    assert len(calls) > calls_before_crosswalk_repair
    repaired = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert set(repaired["CROSSWALK_CHECKSUM"]) != {sha256_file(crosswalk)}
    assert all(
        sha256_file(config.surface_weather.raw_dir / str(row.CROSSWALK_RELATIVE_PATH))
        == row.CROSSWALK_CHECKSUM
        for row in repaired.itertuples(index=False)
    )


def test_existing_sample_snapshot_copies_candidate_references(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls)
    download_surface_weather(config_path, run_id="download-first")
    config = load_meteorological_config(config_path)
    def reject_network(**_kwargs):
        raise AssertionError("snapshot mode must not acquire data")

    from meteorology.surface_weather import download as module

    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", reject_network)
    snapshot_root = tmp_path / "candidate/data/raw/weather"
    working = snapshot_root / "HRRR_R5_WORKING_INVENTORY.parquet"
    inventory = snapshot_root / "HRRR_R5_SOURCE_INVENTORY.parquet"
    manifest = snapshot_root / "R5_DOWNLOAD_MANIFEST.json"
    summary = snapshot_existing_surface_weather_acquisition(
        config_path,
        working_inventory_path=working,
        inventory_path=inventory,
        manifest_path=manifest,
        run_id="existing-snapshot",
    )
    assert summary["snapshot_mode"] == "existing_validated_r5_samples"
    assert summary["complete_times"] == 6
    assert working.exists() and inventory.exists() and manifest.exists()
    load_manifest(manifest, verify_artifacts=True)
    assert validate_product(manifest)["valid"]
    copied_inventory = pq.read_table(inventory).to_pandas()
    assert all(
        (snapshot_root / str(relative)).exists()
        for field in ("RELATIVE_PATH", "CROSSWALK_RELATIVE_PATH")
        for relative in copied_inventory[field]
    )
    config.surface_weather.working_inventory_path.unlink()
    config.surface_weather.inventory_path.unlink()
    with pytest.raises(FileNotFoundError, match="Cannot reconstruct actual HRRR retrieval provenance"):
        snapshot_existing_surface_weather_acquisition(
            config_path,
            working_inventory_path=snapshot_root / "unproven-working.parquet",
            inventory_path=snapshot_root / "unproven-inventory.parquet",
            manifest_path=snapshot_root / "unproven-manifest.json",
        )


def test_network_free_snapshot_refuses_legacy_or_unbound_policy_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    download_surface_weather(config_path, max_workers=1, run_id="source")
    config = load_meteorological_config(config_path)
    source_manifest = config.surface_weather.acquisition_manifest_path
    source_inventory = config.surface_weather.inventory_path
    original_manifest = source_manifest.read_bytes()
    original_inventory = source_inventory.read_bytes()
    candidate = tmp_path / "candidate-raw"
    destination = dict(
        working_inventory_path=candidate / "WORKING.parquet",
        inventory_path=candidate / "INVENTORY.parquet",
        manifest_path=candidate / "MANIFEST.json",
    )
    manifest = json.loads(original_manifest)
    manifest["resolved_config"]["spatial_acceptance_policy"] = "legacy-unverified-policy"
    source_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest|policy|evidence"):
        snapshot_existing_surface_weather_acquisition(config_path, **destination)
    assert not destination["manifest_path"].exists()
    assert source_inventory.read_bytes() == original_inventory
    source_manifest.write_bytes(original_manifest)
    source_manifest.unlink()
    with pytest.raises(ValueError, match="manifest|evidence"):
        snapshot_existing_surface_weather_acquisition(config_path, **destination)
    source_manifest.write_bytes(original_manifest)
    changed = pq.read_table(source_inventory).to_pandas()
    changed.loc[0, "SOURCE_URI"] = "https://example.invalid/tampered"
    pq.write_table(pa.Table.from_pandas(changed, schema=INVENTORY_SCHEMA), source_inventory)
    with pytest.raises(ValueError, match="checksum|evidence"):
        snapshot_existing_surface_weather_acquisition(config_path, **destination)
    assert source_manifest.read_bytes() == original_manifest
    assert not destination["manifest_path"].exists()


@pytest.mark.parametrize("pressure_hpa", [750.0, 1150.0])
def test_rebuild_verifier_uses_shared_pressure_hard_limit(
    tmp_path: Path, monkeypatch, pressure_hpa: float
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    from meteorology.surface_weather import download as module

    def fetch_core(**kwargs):
        valid = pd.Timestamp(kwargs["valid_time_utc"]).tz_convert("UTC")
        grid = _raw_grid(valid)
        grid["MEAN_SEA_LEVEL_PRESSURE_PA"] = pressure_hpa * 100.0
        return grid, hrrr_aws_archive_uri(valid)

    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", fetch_core)
    download_surface_weather(config_path, max_workers=1)
    build_surface_weather(config_path)
    report = verify_hrrr_r5_rebuild(
        config_path, legacy_root=tmp_path / "no-legacy",
        output_path=tmp_path / "verification.json",
    )
    assert report["passed"], report["errors"]
    assert report["pressure_validation"]["regional_warning_counts"][
        "MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN"
    ] > 0


def test_custom_acquisition_destination_copies_references(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="support")
    _patch_fetch(monkeypatch, [])
    candidate = tmp_path / "candidate-raw"
    inventory = candidate / "HRRR_R5_SOURCE_INVENTORY.parquet"
    manifest = candidate / "custom-acquisition.json"
    result = download_surface_weather(
        config_path, max_workers=1,
        working_inventory_path=candidate / "HRRR_R5_WORKING_INVENTORY.parquet",
        inventory_path=inventory, manifest_path=manifest,
    )
    assert result["complete_times"] == 6
    assert validate_product(manifest)["valid"]
    from meteorology.surface_weather.storage import acquisition_lock
    with acquisition_lock(candidate, writer=True):
        with pytest.raises(RuntimeError, match="workspace is busy"):
            validate_product(manifest)
    references = pq.read_table(inventory).to_pandas()
    assert all(
        (candidate / str(relative)).is_file()
        for field in ("RELATIVE_PATH", "CROSSWALK_RELATIVE_PATH")
        for relative in references[field]
    )
    weather_manifest = build_surface_weather(
        config_path, inventory_path=inventory, acquisition_manifest_path=manifest,
        run_id="custom-consumer",
    )[-1]
    assert validate_product(weather_manifest)["valid"]


def test_failed_extension_preserves_canonical_and_resumes_working_inventory(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _fixture_config(tmp_path, end_date="2024-01-03")
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    config = load_meteorological_config(config_path)
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls)
    download_surface_weather(
        config_path, start_date="2024-01-02", end_date="2024-01-02", run_id="day-one"
    )
    inventory_checksum = sha256_file(config.surface_weather.inventory_path)
    manifest_checksum = sha256_file(config.surface_weather.acquisition_manifest_path)

    failed_time = make_sample_times_for_local_date(
        "2024-01-03", config.surface_weather.timezone, 4
    )[-1].isoformat()
    _patch_fetch(monkeypatch, calls, failed={failed_time})
    with pytest.raises(RuntimeError, match="canonical acquisition artifacts were not replaced"):
        download_surface_weather(
            config_path, start_date="2024-01-03", end_date="2024-01-03", run_id="day-two-fail"
        )
    assert sha256_file(config.surface_weather.inventory_path) == inventory_checksum
    assert sha256_file(config.surface_weather.acquisition_manifest_path) == manifest_checksum
    working = pq.read_table(config.surface_weather.working_inventory_path).to_pandas()
    assert len(working) == 12
    assert set(working["STATUS"]) == {"COMPLETE", "FAILED"}

    calls_before_retry = len(calls)
    _patch_fetch(monkeypatch, calls)
    download_surface_weather(
        config_path, start_date="2024-01-03", end_date="2024-01-03", run_id="day-two-retry"
    )
    assert len(calls) == calls_before_retry + 1
    inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert len(inventory) == 12
    assert set(inventory["STATUS"]) == {"COMPLETE"}


@pytest.mark.parametrize("defect", ["missing_field", "non_finite"])
def test_missing_or_non_finite_source_values_fail_closed(
    tmp_path: Path, monkeypatch, defect: str
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    config = load_meteorological_config(config_path)
    failed_time = make_sample_times_for_local_date(
        "2024-01-02", config.surface_weather.timezone, 4
    )[0]

    def fetch(**kwargs):
        valid = pd.Timestamp(kwargs["valid_time_utc"]).tz_convert("UTC")
        frame = _raw_grid(valid)
        if valid == failed_time:
            if defect == "missing_field":
                frame = frame.drop(columns="MEAN_SEA_LEVEL_PRESSURE_PA")
            else:
                frame.loc[0, "TEMPERATURE_2M_K"] = np.inf
        return frame, hrrr_aws_archive_uri(valid)

    from meteorology.surface_weather import download as module

    _patch_fetch(monkeypatch, [])
    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", fetch)
    with pytest.raises(RuntimeError, match="canonical acquisition artifacts were not replaced"):
        download_surface_weather(config_path, run_id=f"fixture-{defect}")
    working = pq.read_table(config.surface_weather.working_inventory_path).to_pandas()
    assert working["STATUS"].value_counts().to_dict() == {"COMPLETE": 5, "FAILED": 1}
    assert not config.surface_weather.inventory_path.exists()
    assert not config.surface_weather.acquisition_manifest_path.exists()


def test_latest_complete_range_is_frozen_across_failed_resume(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path, end_date="latest_complete")
    monkeypatch.setattr(SurfaceWeatherConfig, "resolved_end_date", lambda _self: "2024-01-02")
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    config = load_meteorological_config(config_path)
    failed_time = make_sample_times_for_local_date(
        "2024-01-02", config.surface_weather.timezone, 4
    )[0].isoformat()
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls, failed={failed_time})
    with pytest.raises(RuntimeError):
        download_surface_weather(config_path, run_id="frozen-fail")
    freeze_path = config.surface_weather.raw_dir / "R5_BACKFILL_FREEZE.json"
    assert yaml.safe_load(freeze_path.read_text())["end_date"] == "2024-01-02"

    monkeypatch.setattr(SurfaceWeatherConfig, "resolved_end_date", lambda _self: "2024-01-03")
    _patch_fetch(monkeypatch, calls)
    download_surface_weather(config_path, run_id="frozen-resume")
    inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert len(inventory) == 6
    assert not freeze_path.exists()


def test_transient_herbie_failure_is_retried_per_timestamp(tmp_path: Path, monkeypatch) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    config = load_meteorological_config(config_path)
    retry_time = make_sample_times_for_local_date("2024-01-02", config.surface_weather.timezone, 4)[
        0
    ]
    attempts: dict[str, int] = {}

    def fetch(**kwargs):
        valid = pd.Timestamp(kwargs["valid_time_utc"]).tz_convert("UTC")
        key = valid.isoformat()
        attempts[key] = attempts.get(key, 0) + 1
        if valid == retry_time and attempts[key] < 3:
            raise RuntimeError("temporary archive error")
        return _raw_grid(valid), hrrr_aws_archive_uri(valid)

    from meteorology.surface_weather import download as module

    _patch_fetch(monkeypatch, [])
    monkeypatch.setattr(module, "fetch_cropped_hrrr_grid", fetch)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    summary = download_surface_weather(config_path, run_id="retry-success")
    assert summary["failed_times"] == 0
    assert attempts[retry_time.isoformat()] == 3
