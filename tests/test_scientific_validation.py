"""End-to-end software gate using the package's synthetic producer releases."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil

import h3
import pandas as pd
import pyarrow.parquet as pq
import pytest

from meteorology.artifacts import load_manifest, resolve_portable_path, sha256_file
from meteorology.cli import initialize_workspace
from meteorology.config import load_meteorological_config
from meteorology.offline_example import build_offline_example
from meteorology.releases import freeze_release
from meteorology.scientific_validation import run


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path, monkeypatch) -> dict:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("METEOROLOGY_WORKSPACE", str(workspace))
    initialize_workspace(workspace)
    build_offline_example()
    config = load_meteorological_config()
    releases = tmp_path / "releases"
    manifests = {
        "weather": freeze_release(config.surface_weather.manifest_path, releases),
        "daylight": freeze_release(config.daylight.manifest_path, releases),
        "lunar": freeze_release(config.lunar.manifest_path, releases),
    }
    weather = load_manifest(manifests["weather"])
    inventory_path = resolve_portable_path(
        weather["inputs"][0]["path"], base=manifests["weather"].parent
    )
    inventory = pq.read_table(inventory_path).to_pandas()
    row = inventory.iloc[0]
    sample = pq.read_table(inventory_path.parent / row["RELATIVE_PATH"]).to_pandas().iloc[0]
    cell = str(sample["H3_INDEX"])
    lat, lon = h3.cell_to_latlng(cell)
    site = {
        "site_id": "fixture-station",
        "latitude": lat,
        "longitude": lon,
        "elevation_m": 10,
        "region": "test-region",
        "platform": "synthetic_station",
        "wind_height_m": 10,
        "wind_basis": "earth_relative",
        "source_site_uri": "synthetic://site",
    }
    refs = tmp_path / "references"
    refs.mkdir()
    _write_csv(refs / "sites.csv", list(site), [site])
    valid = str(row["VALID_TIME_UTC"])

    def measurement(
        identifier: str,
        metric: str,
        value: float,
        unit: str,
        definition: str,
        height: str = "",
        start: str = "",
        end: str = "",
    ) -> dict:
        return {
            "record_id": identifier,
            "site_id": site["site_id"],
            "valid_time_utc": valid,
            "metric": metric,
            "value": value,
            "unit": unit,
            "definition": definition,
            "quality_flag": "pass",
            "height_m": height,
            "interval_start_utc": start,
            "interval_end_utc": end,
        }

    weather_rows = [
        measurement(
            "temp",
            "TEMPERATURE_2M_C",
            float(sample["TEMPERATURE_2M_C"]) + 2,
            "degC",
            "air_temperature_2m",
            "2",
        ),
        measurement("u", "U_WIND_10M_MS", 2, "m/s", "earth_relative_wind_10m", "10"),
        measurement("v", "V_WIND_10M_MS", -3, "m/s", "earth_relative_wind_10m", "10"),
        measurement(
            "precip",
            "PRECIP_4H_MM",
            1,
            "mm",
            "gauge_4h_accumulation",
            start=(pd.Timestamp(valid) - pd.Timedelta(hours=4)).isoformat(),
            end=valid,
        ),
        measurement("bad-cloud", "TOTAL_CLOUD_COVER_PCT", 50, "percent", "octa_sky_cover"),
    ]
    _write_csv(refs / "weather.csv", list(weather_rows[0]), weather_rows)
    daylight = load_manifest(manifests["daylight"])
    daily_path = resolve_portable_path(
        daylight["artifacts"][0]["path"], base=manifests["daylight"].parent
    )
    daily = pq.read_table(daily_path).to_pandas().iloc[0]
    lunar = load_manifest(manifests["lunar"])
    lunar_path = resolve_portable_path(
        lunar["artifacts"][0]["path"], base=manifests["lunar"].parent
    )
    lunar_daily = pq.read_table(lunar_path).to_pandas().iloc[0]
    astronomy_rows = [
        {
            "record_id": "daylight",
            "h3_index": daily["H3_INDEX"],
            "date": daily["DATE"],
            "valid_time_utc": "",
            "metric": "DAYLIGHT_HOURS",
            "value": float(daily["DAYLIGHT_HOURS"]) + 1,
            "unit": "hour",
            "latitude": daily["CENTROID_LAT"],
            "longitude": daily["CENTROID_LON"],
            "quality_flag": "pass",
            "case_tag": "ordinary_day",
        },
        {
            "record_id": "solar-alt",
            "h3_index": daily["H3_INDEX"],
            "date": daily["DATE"],
            "valid_time_utc": "2024-01-02T20:00:00+00:00",
            "metric": "SOLAR_ALTITUDE_DEG_AT_UTC",
            "value": 0,
            "unit": "degree",
            "latitude": daily["CENTROID_LAT"],
            "longitude": daily["CENTROID_LON"],
            "quality_flag": "pass",
            "case_tag": "ordinary_day",
        },
        {
            "record_id": "illumination",
            "h3_index": lunar_daily["H3_INDEX"],
            "date": lunar_daily["DATE"],
            "valid_time_utc": "",
            "metric": "LUNAR_ILLUMINATION_FRACTION",
            "value": 0.5,
            "unit": "fraction",
            "latitude": lunar_daily["CENTROID_LAT"],
            "longitude": lunar_daily["CENTROID_LON"],
            "quality_flag": "pass",
            "case_tag": "ordinary_day",
        },
        {
            "record_id": "phase",
            "h3_index": lunar_daily["H3_INDEX"],
            "date": lunar_daily["DATE"],
            "valid_time_utc": "",
            "metric": "LUNAR_PHASE_ANGLE_DEG",
            "value": 359,
            "unit": "degree",
            "latitude": lunar_daily["CENTROID_LAT"],
            "longitude": lunar_daily["CENTROID_LON"],
            "quality_flag": "pass",
            "case_tag": "ordinary_day",
        },
        {
            "record_id": "moon-alt",
            "h3_index": lunar_daily["H3_INDEX"],
            "date": lunar_daily["DATE"],
            "valid_time_utc": "2024-01-02T20:00:00+00:00",
            "metric": "MOON_ALTITUDE_DEG_AT_UTC",
            "value": 0,
            "unit": "degree",
            "latitude": lunar_daily["CENTROID_LAT"],
            "longitude": lunar_daily["CENTROID_LON"],
            "quality_flag": "pass",
            "case_tag": "ordinary_day",
        },
    ]
    _write_csv(refs / "astronomy.csv", list(astronomy_rows[0]), astronomy_rows)
    source = {
        "name": "synthetic software fixture",
        "uri": "synthetic://references",
        "retrieved_at_utc": "2026-10-01T00:00:00+00:00",
        "license": "Apache-2.0",
        "attribution": "test fixture",
        "redistribution_restrictions": "none",
        "may_share_assimilation_inputs": None,
    }

    def spec(name: str, with_source: bool = True) -> dict:
        value = {"path": name, "sha256": sha256_file(refs / name)}
        return {**value, "source": source} if with_source else value

    family_keys = {
        "daylight": ("timezone", "timestep_minutes", "low_sun_max_degrees", "leap_day_policy"),
        "lunar": (
            "timezone",
            "timestep_minutes",
            "sample_hour_utc",
            "dark_sun_altitude_deg",
            "moon_altitude_min_deg",
        ),
    }
    bundle = {
        "schema_version": 1,
        "evidence_class": "synthetic_fixture",
        "sites": spec("sites.csv", False),
        "weather": spec("weather.csv"),
        "astronomy": spec("astronomy.csv"),
        "astronomy_conventions": {
            "horizon": "geometric",
            "refraction": "none",
            **{
                family: {key: manifest["resolved_config"][key] for key in family_keys[family]}
                for family, manifest in (("daylight", daylight), ("lunar", lunar))
            },
        },
    }
    bundle_path = refs / "bundle.json"
    bundle_path.write_text(json.dumps(bundle))
    metric_names = [
        "TEMPERATURE_2M_C",
        "U_WIND_10M_MS",
        "V_WIND_10M_MS",
        "PRECIP_4H_MM",
        "TOTAL_CLOUD_COVER_PCT",
        "WIND_SPEED_10M_MS",
        "WIND_DIRECTION_10M_DEG",
        "DAYLIGHT_HOURS",
        "SOLAR_ALTITUDE_DEG_AT_UTC",
        "LUNAR_ILLUMINATION_FRACTION",
        "LUNAR_PHASE_ANGLE_DEG",
        "MOON_ALTITUDE_DEG_AT_UTC",
    ]
    threshold = {
        "min_pairs": 1,
        "max_abs_bias": 10,
        "max_mae": 10,
        "max_rmse": 10,
        "rationale": "Wide synthetic tolerance to exercise software decisions only",
    }
    protocol = {
        "schema_version": 1,
        "name": "offline-fixture-v1",
        "locked_at_utc": "2026-09-01T00:00:00+00:00",
        "rationale": "Software fixture with deterministic producer values; no empirical claim",
        "holdout": {
            "site_ids": [site["site_id"]],
            "start_utc": "2024-01-01T00:00:00+00:00",
            "end_utc": "2024-01-04T00:00:00+00:00",
            "astronomy_case_tags": ["ordinary_day"],
        },
        "pairing": {
            "max_time_delta_minutes": 1,
            "max_centroid_distance_m": 100,
            "max_source_distance_m": 100,
            "max_astronomy_coordinate_offset_m": 100,
            "height_tolerance_m": 0.1,
            "calm_speed_threshold_ms": 0.5,
            "wet_threshold_mm": 0.1,
            "precip_intensity_edges_mm": [1, 5],
        },
        "accepted_quality_flags": ["pass"],
        "thresholds": {name: threshold for name in metric_names},
    }
    protocol_path = refs / "protocol.json"
    protocol_path.write_text(json.dumps(protocol))
    return {
        "manifests": manifests,
        "bundle": bundle_path,
        "protocol": protocol_path,
        "references": refs,
        "workspace": workspace,
    }


def _run(fixture: dict, output: Path) -> Path:
    return run(
        weather_manifest=fixture["manifests"]["weather"],
        daylight_manifest=fixture["manifests"]["daylight"],
        lunar_manifest=fixture["manifests"]["lunar"],
        reference_bundle=fixture["bundle"],
        protocol_path=fixture["protocol"],
        output_dir=output,
    )


def _rewrite_reference(fixture: dict, name: str, rows: list[dict]) -> None:
    path = fixture["references"] / name
    _write_csv(path, list(rows[0]), rows)
    bundle = json.loads(fixture["bundle"].read_text())
    bundle["weather" if name == "weather.csv" else "astronomy"]["sha256"] = sha256_file(path)
    fixture["bundle"].write_text(json.dumps(bundle))


def test_offline_comparison_reports_pairs_exclusions_and_relocation(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    relocated = tmp_path / "relocated"
    shutil.move(str(fixture["manifests"]["weather"].parent.parent), str(relocated))
    for family, path in fixture["manifests"].items():
        fixture["manifests"][family] = relocated / path.parent.name / "MANIFEST.json"
    shutil.move(str(fixture["workspace"]), str(tmp_path / "hidden-workspace"))
    output = _run(fixture, tmp_path / "comparison")
    result = json.loads((output / "results.json").read_text())
    pairs = pq.read_table(output / "paired.parquet").to_pandas()
    excluded = pq.read_table(output / "excluded.parquet").to_pandas()
    assert result["summary"]["evidence_gate"] == "SOFTWARE_ONLY"
    assert result["input_counts"] == {"weather": 5, "astronomy": 5}
    assert result["summary"]["metrics"]["TEMPERATURE_2M_C"]["bias"] == pytest.approx(-2)
    assert result["summary"]["metrics"]["PRECIP_4H_MM"]["decision"] == "SAMPLING_SENSITIVITY"
    assert result["summary"]["precipitation"]["occurrence"]["true_positive"] == 1
    assert {
        "WIND_SPEED_10M_MS",
        "WIND_DIRECTION_10M_DEG",
        "DAYLIGHT_HOURS",
        "SOLAR_ALTITUDE_DEG_AT_UTC",
        "LUNAR_ILLUMINATION_FRACTION",
        "LUNAR_PHASE_ANGLE_DEG",
        "MOON_ALTITUDE_DEG_AT_UTC",
    }.issubset(set(pairs["metric"]))
    assert abs(pairs.loc[pairs["metric"] == "LUNAR_PHASE_ANGLE_DEG", "signed_error"].iloc[0]) <= 180
    assert "unit_or_definition_mismatch" in set(excluded["reason"])
    assert result["summary"]["astronomy_case_coverage"]["missing"]
    assert "SOFTWARE_ONLY" in (output / "REPORT.md").read_text()
    with pytest.raises(FileExistsError):
        _run(fixture, output)


def test_reference_checksum_failure_does_not_publish(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    with (fixture["references"] / "weather.csv").open("a") as handle:
        handle.write("\n")
    output = tmp_path / "comparison"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _run(fixture, output)
    assert not output.exists()


def test_threshold_failure_and_incompatible_reference_exclusions(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    path = fixture["references"] / "weather.csv"
    rows = pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records")
    for row in rows:
        if row["record_id"] == "temp":
            row["value"] = str(float(row["value"]) + 100)
        elif row["record_id"] in {"u", "v"}:
            row["value"] = "0"
        elif row["record_id"] == "precip":
            row["interval_start_utc"] = row["valid_time_utc"]
    _rewrite_reference(fixture, "weather.csv", rows)
    output = _run(fixture, tmp_path / "comparison")
    result = json.loads((output / "results.json").read_text())
    reasons = set(pq.read_table(output / "excluded.parquet").to_pandas()["reason"])
    assert result["summary"]["threshold_gate"] == "FAIL"
    assert result["summary"]["metrics"]["TEMPERATURE_2M_C"]["decision"] == "FAIL"
    assert result["summary"]["metrics"]["WIND_DIRECTION_10M_DEG"]["decision"] == "NOT_EVALUATED"
    assert {
        "incompatible_precip_interval",
        "calm_wind_direction",
        "unit_or_definition_mismatch",
    } <= reasons
    assert result["summary"]["precipitation"]["status"] == "NOT_EVALUATED"


def test_missing_threshold_or_wrong_conventions_cannot_pass(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    protocol = json.loads(fixture["protocol"].read_text())
    del protocol["thresholds"]["DAYLIGHT_HOURS"]
    fixture["protocol"].write_text(json.dumps(protocol))
    output = tmp_path / "comparison"
    with pytest.raises(ValueError, match="lacks prespecified thresholds"):
        _run(fixture, output)
    assert not output.exists()
    protocol["thresholds"]["DAYLIGHT_HOURS"] = next(iter(protocol["thresholds"].values()))
    fixture["protocol"].write_text(json.dumps(protocol))
    bundle = json.loads(fixture["bundle"].read_text())
    bundle["astronomy_conventions"]["refraction"] = "standard"
    fixture["bundle"].write_text(json.dumps(bundle))
    result_path = _run(fixture, output)
    result = json.loads((result_path / "results.json").read_text())
    assert result["summary"]["threshold_gate"] == "NOT_EVALUATED"
    assert result["summary"]["exclusion_reasons"]["convention_mismatch"] == 5
