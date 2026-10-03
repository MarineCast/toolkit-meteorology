"""End-to-end offline hourly producer, lineage, relocation, and failure tests."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import box

from meteorology.artifacts import checksum_path, write_table
from meteorology.cli import initialize_workspace, main as cli_main
from meteorology.config import load_meteorological_config
from meteorology.hourly_weather.product import (
    _paths, acquire_hourly_weather, build_hourly_weather, validate_hourly_product,
    retain_decoded_hourly_grid,
)
from meteorology.releases import freeze_release
from meteorology.spatial_support.build import (
    build_meteorological_spatial_support, load_meteorological_support,
)
from meteorology.surface_weather.source import RAW_SCHEMA, _grid_hash
from meteorology.temporal_products import hourly_utc_instants, summarize_hourly_window
from meteorology.validation import validate_product


def _setup(tmp_path: Path, monkeypatch, *, day: str = "2024-01-02") -> tuple[Path, Path, int]:
    monkeypatch.setenv("METEOROLOGY_WORKSPACE", str(tmp_path))
    initialize_workspace(tmp_path)
    config_path = tmp_path / "config/data/environment_meteorological.yaml"
    build_meteorological_spatial_support(config_path, run_id="hourly-fixture-support")
    config = load_meteorological_config(config_path)
    support = load_meteorological_support(5, config_path)
    grid_hash = _grid_hash(support["CENTROID_LAT"], support["CENTROID_LON"])
    decoded = tmp_path / "decoded"
    decoded.mkdir()
    footprint = box(config.bbox["min_lon"] - 0.1, config.bbox["min_lat"] - 0.1,
                    config.bbox["max_lon"] + 0.1, config.bbox["max_lat"] + 0.1)
    for index, valid in enumerate(hourly_utc_instants(day, config.surface_weather.timezone)):
        frame = pd.DataFrame({
            "SOURCE_GRID_INDEX": np.arange(len(support), dtype="int32"),
            "SOURCE_LAT": support["CENTROID_LAT"].to_numpy(dtype=float),
            "SOURCE_LON": support["CENTROID_LON"].to_numpy(dtype=float),
            "VALID_TIME_UTC": valid.isoformat(), "INIT_TIME_UTC": valid.isoformat(),
            "AVAILABLE_AT_UTC": (valid + timedelta(hours=6)).isoformat(),
            "SOURCE_MODEL": "synthetic_hrrr", "SOURCE_PRODUCT": "sfc",
            "FORECAST_HOUR": 0, "SOURCE_GRID_HASH": grid_hash,
            "SOURCE_WIND_BASIS": "earth_relative",
            "TEMPERATURE_2M_K": 280.15 + index,
            "RELATIVE_HUMIDITY_2M_PCT": 60.0,
            "U_WIND_10M_MS": 0.0, "V_WIND_10M_MS": 0.0,
            "WIND_GUST_SURFACE_MS": 0.0, "VISIBILITY_M": 15_000.0,
            "TOTAL_CLOUD_COVER_PCT": 0.0,
            "PRECIP_RATE_KG_M2_S": 0.0,
            "MEAN_SEA_LEVEL_PRESSURE_PA": 101_000.0,
        })
        stem = valid.strftime("%Y%m%dT%HZ")
        write_table(decoded / f"{stem}.parquet", pa.Table.from_pandas(frame, preserve_index=False),
                    RAW_SCHEMA)
        (decoded / f"{stem}.json").write_text(json.dumps({
            "valid_time_utc": valid.isoformat(), "source_grid_hash": grid_hash,
            "source_uri": f"synthetic://fixture/{stem}",
            "source_object_uri": f"synthetic://fixture/{stem}",
            "retrieved_at_utc": (valid + timedelta(hours=1)).isoformat(),
            "source_evidence_kind": "synthetic_fixture",
            "native_footprint_wkb_hex": footprint.wkb_hex,
            "max_nearest_distance_m": 1000.0,
        }), encoding="utf-8")
    return config_path, decoded, len(support)


def test_hourly_offline_acquire_build_validate_freeze_and_relocate(tmp_path: Path,
                                                                   monkeypatch, capsys) -> None:
    config_path, decoded, cells = _setup(tmp_path / "workspace", monkeypatch)
    assert cli_main(["--workspace", str(tmp_path / "workspace"), "download", "hourly-weather",
                     "--date", "2024-01-02", "--decoded-dir", str(decoded), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["source_requests"] == 0
    preview = acquire_hourly_weather(config_path, local_date="2024-01-02",
                                     decoded_dir=decoded, dry_run=True)
    assert preview["expected_cycles"] == 24 and preview["source_requests"] == 0
    assert preview["decoded_input_bytes"] > 0
    acquired = acquire_hourly_weather(config_path, local_date="2024-01-02", decoded_dir=decoded)
    assert validate_product(acquired["manifest"])["row_count"] == 24 * cells
    manifest = build_hourly_weather(config_path)
    assert validate_hourly_product(manifest)["row_count"] == 24 * cells
    assert validate_product(manifest)["source_evidence_kind"] == "synthetic_fixture"
    assert cli_main(["--workspace", str(tmp_path / "workspace"), "inspect", "hourly-weather"]) == 0
    assert json.loads(capsys.readouterr().out)["expected_hours"] == 24
    product = pq.read_table(_paths(load_meteorological_config(config_path))[3]
                            / "H3_HOURLY_WEATHER_RES_5/date=2024-01-02/part-000.parquet").to_pandas()
    assert product["WIND_SPEED_10M_MS"].eq(0).all()
    assert "PRECIP_RATE_MM_HR" not in product
    cell = product["H3_INDEX"].iloc[0]
    selected = product.loc[product["H3_INDEX"] == cell].to_dict("records")
    start, end = selected[0]["VALID_TIME_UTC"], selected[4]["VALID_TIME_UTC"]
    summary = summarize_hourly_window(selected, start_utc=start, end_utc=end,
                                      field="WIND_SPEED_10M_MS", h3_index=cell)
    assert summary["valid_hours"] == 4 and summary["sampled_mean"] == 0
    assert summarize_hourly_window(selected, start_utc=start, end_utc=end,
                                   field="WIND_SPEED_10M_MS", h3_index=cell,
                                   as_of_utc=start)["valid_hours"] == 0
    frozen = freeze_release(manifest, tmp_path / "releases")
    moved = tmp_path / "frozen" / frozen.parent.name
    moved.parent.mkdir()
    frozen.parent.rename(moved)
    (tmp_path / "workspace").rename(tmp_path / "unavailable-original")
    assert validate_product(moved / "MANIFEST.json")["row_count"] == 24 * cells


def test_hourly_missing_hour_preserves_previous_metadata_and_resumes(tmp_path: Path,
                                                                     monkeypatch) -> None:
    config_path, decoded, _ = _setup(tmp_path / "workspace", monkeypatch)
    config = load_meteorological_config(config_path)
    raw_dir, inventory, manifest, _ = _paths(config)
    target = sorted(decoded.glob("*.json"))[4]
    target.rename(target.with_suffix(".missing"))
    with pytest.raises(FileNotFoundError, match="incomplete"):
        acquire_hourly_weather(config_path, local_date="2024-01-02", decoded_dir=decoded)
    assert not inventory.exists() and not manifest.exists()
    target.with_suffix(".missing").rename(target)
    acquire_hourly_weather(config_path, local_date="2024-01-02", decoded_dir=decoded)
    original = {path: checksum_path(path) for path in (inventory, manifest)}
    prior_objects = {path: checksum_path(path) for path in (raw_dir / "objects").rglob("*.parquet")}
    assert prior_objects
    broken = sorted(decoded.glob("*.parquet"))[5]
    broken.write_bytes(b"corrupt")
    with pytest.raises(Exception):
        acquire_hourly_weather(config_path, local_date="2024-01-02", decoded_dir=decoded)
    assert {path: checksum_path(path) for path in original} == original
    assert {path: checksum_path(path) for path in prior_objects} == prior_objects


@pytest.mark.parametrize("day,expected", [("2024-03-10", 23), ("2024-11-03", 25)])
def test_hourly_product_keeps_true_dst_civil_day(tmp_path: Path, monkeypatch,
                                                   day: str, expected: int) -> None:
    config_path, decoded, cells = _setup(tmp_path / "workspace", monkeypatch, day=day)
    result = acquire_hourly_weather(config_path, local_date=day, decoded_dir=decoded)
    assert result["expected_cycles"] == expected
    product = build_hourly_weather(config_path)
    assert validate_product(product)["row_count"] == expected * cells


def test_hourly_rejects_missing_cell_bad_availability_and_corrupt_object(tmp_path: Path,
                                                                          monkeypatch) -> None:
    config_path, decoded, _ = _setup(tmp_path / "workspace", monkeypatch)
    config = load_meteorological_config(config_path)
    _, _, acquisition_manifest, _ = _paths(config)
    source = sorted(decoded.glob("*.parquet"))[0]
    original = source.read_bytes()
    frame = pq.read_table(source).to_pandas()
    write_table(source, pa.Table.from_pandas(frame.iloc[1:], preserve_index=False), RAW_SCHEMA)
    with pytest.raises(ValueError, match="native points|too far|support|crosswalk"):
        acquire_hourly_weather(config_path, local_date="2024-01-02", decoded_dir=decoded)
    assert not acquisition_manifest.exists()
    source.write_bytes(original)
    frame["AVAILABLE_AT_UTC"] = "2024-01-01T00:00:00+00:00"
    write_table(source, pa.Table.from_pandas(frame, preserve_index=False), RAW_SCHEMA)
    with pytest.raises(ValueError, match="availability"):
        acquire_hourly_weather(config_path, local_date="2024-01-02", decoded_dir=decoded)
    source.write_bytes(original)
    result = acquire_hourly_weather(config_path, local_date="2024-01-02", decoded_dir=decoded)
    import meteorology.hourly_weather.product as product_module

    acquisition = json.loads(Path(result["manifest"]).read_text())
    sample = next(Path(item["path"]) for item in acquisition["inputs"] if item["role"] == "sample")
    sample.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        product_module.validate_hourly_product(result["manifest"])


def test_hourly_interrupted_ingest_reuses_immutable_objects(tmp_path: Path, monkeypatch) -> None:
    config_path, decoded, _ = _setup(tmp_path / "workspace", monkeypatch)
    import meteorology.hourly_weather.product as product_module

    original_load = product_module._load_decoded_grid
    calls = 0

    def fail_after_two(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("injected decoded-input interruption")
        return original_load(*args, **kwargs)

    monkeypatch.setattr(product_module, "_load_decoded_grid", fail_after_two)
    with pytest.raises(RuntimeError, match="interruption"):
        acquire_hourly_weather(config_path, local_date="2024-01-02", decoded_dir=decoded)
    raw, _, manifest, _ = _paths(load_meteorological_config(config_path))
    assert not manifest.exists()
    prior = {path: checksum_path(path) for path in (raw / "objects").rglob("*.parquet")}
    assert prior
    monkeypatch.setattr(product_module, "_load_decoded_grid", original_load)
    result = acquire_hourly_weather(config_path, local_date="2024-01-02", decoded_dir=decoded)
    assert validate_product(result["manifest"])["valid"]
    assert {path: checksum_path(path) for path in prior} == prior


def test_retention_helper_uses_existing_normalized_decoder_contract(tmp_path: Path,
                                                                   monkeypatch) -> None:
    config_path, decoded, _ = _setup(tmp_path / "workspace", monkeypatch)
    config = load_meteorological_config(config_path)
    source = sorted(decoded.glob("*.parquet"))[0]
    evidence = json.loads(source.with_suffix(".json").read_text())
    frame = pq.read_table(source).to_pandas()
    frame.attrs["native_footprint"] = box(
        config.bbox["min_lon"] - 0.1, config.bbox["min_lat"] - 0.1,
        config.bbox["max_lon"] + 0.1, config.bbox["max_lat"] + 0.1,
    )
    frame.attrs["max_nearest_distance_m"] = 1000.0
    retained, sidecar = retain_decoded_hourly_grid(
        frame, valid_time_utc=evidence["valid_time_utc"],
        source_uri=evidence["source_uri"],
        retrieved_at_utc=evidence["retrieved_at_utc"],
        output_dir=tmp_path / "retained", source_evidence_kind="synthetic_fixture",
    )
    assert pq.read_schema(retained).equals(RAW_SCHEMA, check_metadata=False)
    assert json.loads(sidecar.read_text())["source_object_uri"].startswith("synthetic://")
    assert (retained, sidecar) == retain_decoded_hourly_grid(
        frame, valid_time_utc=evidence["valid_time_utc"], source_uri=evidence["source_uri"],
        retrieved_at_utc=evidence["retrieved_at_utc"], output_dir=tmp_path / "retained",
        source_evidence_kind="synthetic_fixture")


def test_hourly_build_rejects_changed_or_rebuilt_spatial_support(tmp_path: Path, monkeypatch) -> None:
    config_path, decoded, _ = _setup(tmp_path / "workspace", monkeypatch)
    acquired = acquire_hourly_weather(config_path, local_date="2024-01-02", decoded_dir=decoded)
    product = build_hourly_weather(config_path)
    assert validate_hourly_product(product)["valid"]
    before = checksum_path(product)
    common_path = config_path.parents[1] / "common.yaml"
    original = common_path.read_text()
    common_path.write_text(original.replace("max_lat: 49.70", "max_lat: 49.50"))
    with pytest.raises(ValueError, match="support|bounding box|spatial"):
        build_hourly_weather(config_path)
    assert checksum_path(product) == before
    build_meteorological_spatial_support(config_path)
    with pytest.raises(ValueError, match="support|spatial"):
        build_hourly_weather(config_path)
    assert checksum_path(product) == before
    assert acquired["source_evidence_kind"] == "synthetic_fixture"


def test_retained_bundle_failure_has_no_final_half_pair(tmp_path: Path, monkeypatch) -> None:
    config_path, decoded, _ = _setup(tmp_path / "workspace", monkeypatch)
    source = sorted(decoded.glob("*.parquet"))[0]
    frame = pq.read_table(source).to_pandas()
    metadata = json.loads(source.with_suffix(".json").read_text())
    config = load_meteorological_config(config_path)
    frame.attrs["native_footprint"] = box(
        config.bbox["min_lon"] - 0.1, config.bbox["min_lat"] - 0.1,
        config.bbox["max_lon"] + 0.1, config.bbox["max_lat"] + 0.1)
    frame.attrs["max_nearest_distance_m"] = 1000.0
    import meteorology.hourly_weather.product as module

    original_json = module.atomic_write_json
    monkeypatch.setattr(module, "atomic_write_json", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("injected sidecar failure")))
    output = tmp_path / "retained"
    kwargs = dict(valid_time_utc=metadata["valid_time_utc"], source_uri=metadata["source_uri"],
                  retrieved_at_utc=metadata["retrieved_at_utc"], output_dir=output,
                  source_evidence_kind="synthetic_fixture")
    with pytest.raises(RuntimeError, match="sidecar failure"):
        retain_decoded_hourly_grid(frame, **kwargs)
    assert not list(output.rglob("*.parquet")) and not list(output.rglob("*.json"))
    monkeypatch.setattr(module, "atomic_write_json", original_json)
    parquet_path, evidence_path = retain_decoded_hourly_grid(frame, **kwargs)
    assert parquet_path.is_file() and evidence_path.is_file()
    original = (checksum_path(parquet_path), checksum_path(evidence_path))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retain_decoded_hourly_grid(frame, **kwargs), range(2)))
    assert results == [(parquet_path, evidence_path)] * 2
    assert (checksum_path(parquet_path), checksum_path(evidence_path)) == original
    with pytest.raises(FileExistsError, match="different bytes"):
        retain_decoded_hourly_grid(frame, **{**kwargs, "source_uri": "synthetic://changed"})
    assert (checksum_path(parquet_path), checksum_path(evidence_path)) == original


def test_retained_bundle_retries_after_serialization_validation_and_orphan(tmp_path: Path,
                                                                         monkeypatch) -> None:
    config_path, decoded, _ = _setup(tmp_path / "workspace", monkeypatch)
    source = sorted(decoded.glob("*.parquet"))[0]
    frame = pq.read_table(source).to_pandas()
    metadata = json.loads(source.with_suffix(".json").read_text())
    config = load_meteorological_config(config_path)
    frame.attrs["native_footprint"] = box(
        config.bbox["min_lon"] - 0.1, config.bbox["min_lat"] - 0.1,
        config.bbox["max_lon"] + 0.1, config.bbox["max_lat"] + 0.1)
    frame.attrs["max_nearest_distance_m"] = 1000.0
    output = tmp_path / "retained"
    kwargs = dict(valid_time_utc=metadata["valid_time_utc"], source_uri=metadata["source_uri"],
                  retrieved_at_utc=metadata["retrieved_at_utc"], output_dir=output,
                  source_evidence_kind="synthetic_fixture")
    import meteorology.hourly_weather.product as module

    write = module.write_table
    monkeypatch.setattr(module, "write_table", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("injected serialization failure")))
    with pytest.raises(RuntimeError, match="serialization failure"):
        retain_decoded_hourly_grid(frame, **kwargs)
    monkeypatch.setattr(module, "write_table", write)
    assert not list(output.rglob("*.parquet"))
    bad = frame.copy()
    bad.attrs = frame.attrs.copy()
    bad["SOURCE_GRID_HASH"] = "bad-grid-hash"
    with pytest.raises(ValueError, match="grid hash"):
        retain_decoded_hourly_grid(bad, **kwargs)
    assert not list(output.rglob("*.parquet"))
    orphan = output / f'.{source.stem}.dead.candidate'
    orphan.mkdir()
    (orphan / "grid.parquet").write_bytes(b"interrupted")
    retained, sidecar = retain_decoded_hourly_grid(frame, **kwargs)
    assert retained.is_file() and sidecar.is_file()
    assert (orphan / "grid.parquet").read_bytes() == b"interrupted"
