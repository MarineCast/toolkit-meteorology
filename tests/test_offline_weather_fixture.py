from __future__ import annotations

import json
from pathlib import Path

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
from meteorology.daylight.inspect import inspect_daylight
from meteorology.lunar.build import build_lunar
from meteorology.lunar.inspect import inspect_lunar
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
)
from meteorology.surface_weather.verify import (
    SHARED_LEGACY_FIELDS,
    _legacy_field_partial_evidence,
    verify_hrrr_r5_rebuild,
)


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


def _raw_grid(valid: pd.Timestamp) -> pd.DataFrame:
    available = valid + pd.Timedelta(hours=6)
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
) -> None:
    from meteorology.surface_weather import download as module

    def fetch(**kwargs):
        valid = pd.Timestamp(kwargs["valid_time_utc"]).tz_convert("UTC")
        calls.append(valid.isoformat())
        if failed and valid.isoformat() in failed:
            raise RuntimeError("fixture acquisition failure")
        return _raw_grid(valid), hrrr_aws_archive_uri(valid)

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


def test_complete_range_all_zero_forecast_precipitation_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    _patch_fetch(monkeypatch, [], precip_rate=0.0)
    download_surface_weather(config_path, run_id="fixture-download")

    with pytest.raises(ValueError, match="precipitation is all zero"):
        build_surface_weather(config_path, run_id="fixture-all-zero-weather")


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
    assert len(list((config.surface_weather.raw_dir / "crosswalks").rglob("*.parquet"))) == 1
    load_manifest(config.surface_weather.acquisition_manifest_path, verify_artifacts=True)

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
    assert evidence["cloud_cover"]["legacy_selector_state"] == "AMBIGUOUS_TCDC_LEVEL"

    build_daylight(
        config_path, start_date="2024-01-02", end_date="2024-01-02", run_id="fixture-daylight"
    )
    build_lunar(config_path, start_date="2024-01-02", end_date="2024-01-02", run_id="fixture-lunar")
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


@pytest.mark.parametrize("placeholder_uri", ["aws", "existing_validated_local_sample"])
def test_download_resumes_and_repairs_checksum_mismatch(
    tmp_path: Path, monkeypatch, placeholder_uri: str
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls)
    download_surface_weather(config_path, run_id="download-first")
    config = load_meteorological_config(config_path)
    inventory = pq.read_table(config.surface_weather.working_inventory_path).to_pandas()
    inventory["SOURCE_URI"] = placeholder_uri
    write_table(
        config.surface_weather.working_inventory_path,
        pa.Table.from_pandas(inventory, preserve_index=False),
        INVENTORY_SCHEMA,
    )
    download_surface_weather(config_path, run_id="download-resume")
    assert len(calls) == 6
    resumed = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    assert resumed["SOURCE_URI"].str.startswith("https://noaa-hrrr-bdp-pds.s3.amazonaws.com/").all()

    first_time = make_sample_times_for_local_date("2024-01-02", config.surface_weather.timezone, 4)[
        0
    ]
    path = sample_path(config.surface_weather.raw_dir, first_time, config.surface_weather.timezone)
    tampered = pq.ParquetFile(path).read().to_pandas()
    tampered["TEMPERATURE_2M_C"] += 1.0
    write_table(path, pa.Table.from_pandas(tampered, preserve_index=False), SAMPLE_SCHEMA)
    download_surface_weather(config_path, run_id="download-repair")
    assert len(calls) == 7

    inventory = pq.read_table(config.surface_weather.inventory_path).to_pandas()
    crosswalk = config.surface_weather.raw_dir / inventory["CROSSWALK_RELATIVE_PATH"].iloc[0]
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
    assert set(repaired["CROSSWALK_CHECKSUM"]) == {sha256_file(crosswalk)}


def test_existing_sample_snapshot_writes_only_candidate_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _fixture_config(tmp_path)
    build_meteorological_spatial_support(config_path, run_id="fixture-support")
    calls: list[str] = []
    _patch_fetch(monkeypatch, calls)
    download_surface_weather(config_path, run_id="download-first")
    config = load_meteorological_config(config_path)
    config.surface_weather.working_inventory_path.unlink()
    config.surface_weather.inventory_path.unlink()
    config.surface_weather.acquisition_manifest_path.unlink()

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
