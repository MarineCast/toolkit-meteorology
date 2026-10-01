"""Regression gates for method, release, catalog and acquisition safeguards."""

from __future__ import annotations

from pathlib import Path

import pytest

from meteorology import __version__
from meteorology.artifacts import manifest_payload, validate_manifest
from meteorology.methods import METHOD_VERSIONS, method_version
from meteorology.variables import PRODUCTS, catalog


def _fixture_payload(tmp_path: Path, run_id: str) -> dict:
    return manifest_payload(
        product="fixture",
        run_id=run_id,
        config_path=tmp_path / "config.yaml",
        resolved_config={"extent": "small"},
        artifacts=[{"path": str(tmp_path / "data.parquet"), "checksum": "a" * 64}],
        inputs=[{"path": str(tmp_path / "input"), "checksum": "b" * 64}],
    )


def test_release_identity_is_stable_across_runs_and_paths(tmp_path: Path) -> None:
    first = _fixture_payload(tmp_path, "run-a")
    second = _fixture_payload(tmp_path, "run-b")
    second["artifacts"][0]["path"] = str(tmp_path / "moved.parquet")
    assert first["release_id"] == second["release_id"]
    assert first["software_version"] == __version__
    validate_manifest(first, verify_artifacts=False)
    validate_manifest(second, verify_artifacts=False)
    second["artifacts"][0]["checksum"] = "c" * 64
    with pytest.raises(ValueError, match="release_id"):
        validate_manifest(second, verify_artifacts=False)


def test_every_declared_arrow_field_has_catalog_metadata() -> None:
    inventory = catalog()
    assert set(inventory) == set(PRODUCTS)
    for family, (schema, product, _support) in PRODUCTS.items():
        assert [item["name"] for item in inventory[family]] == schema.names
        assert {item["method_version"] for item in inventory[family]} == {method_version(product)}
        for item in inventory[family]:
            assert all(item[key] for key in ("source", "source_variable", "units", "processing_and_aggregation", "missing_value_policy", "interpretation", "limitations"))
    assert method_version("meteorological.surface_weather") == "hrrr_surface_daily_earth_wind_v5"
    assert set(METHOD_VERSIONS) == {product for _, product, _ in PRODUCTS.values()} | {"meteorological.daily_matrix"}


def test_download_range_requires_explicit_intent_before_network(tmp_path: Path) -> None:
    from meteorology.surface_weather.download import download_surface_weather
    import yaml

    config = yaml.safe_load(Path("config/data/environment_meteorological.yaml").read_text())
    path = tmp_path / "meteorological.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    preview = download_surface_weather(path, start_date="2024-01-02", end_date="2024-01-10", dry_run=True)
    assert preview["expected_times"] == 54
    with pytest.raises(ValueError, match="--allow-large-download"):
        download_surface_weather(path, start_date="2024-01-02", end_date="2024-01-10")
