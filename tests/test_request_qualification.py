"""Offline guard checks for the opt-in public-getter qualification procedure."""

import hashlib
import importlib.util
import json
from functools import partial
from pathlib import Path
import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        "qualify_requests", Path(__file__).parents[1] / "demo/qualify_requests.py"
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_config_free_qualification_help(capsys):
    with pytest.raises(SystemExit) as error:
        module().main(["--help"])
    assert error.value.code == 0
    assert "offline dry run" in capsys.readouterr().out


def test_amount_case_requires_independent_nonzero_evidence(tmp_path):
    case = tmp_path / "case.json"
    case.write_text(
        json.dumps(
            dict(
                date="2024-01-01",
                bbox=[-125.25, 49, -125, 49.25],
                independent_reference=dict(
                    file="reference.txt",
                    sha256="0" * 64,
                    uri="fixture://only",
                    date="2024-01-01",
                    bbox=[-125.25, 49, -125, 49.25],
                    verification_statement="synthetic guard fixture",
                    verified_nonzero_parameters=["tp", "sf"],
                ),
            )
        )
    )
    (tmp_path / "reference.txt").write_text("synthetic guard fixture, not scientific validation")
    with pytest.raises(ValueError, match="independently verified"):
        module().load_case(case, True)
    data = json.loads(case.read_text())
    data["independent_reference"]["sha256"] = hashlib.sha256(
        (tmp_path / "reference.txt").read_bytes()
    ).hexdigest()
    case.write_text(json.dumps(data))
    assert module().load_case(case, True)["date"] == "2024-01-01"


def test_qualification_default_is_zero_network_plan(tmp_path, capsys):
    case = tmp_path / "case.json"
    case.write_text(json.dumps(dict(date="2024-01-01", bbox=[-125.25, 49, -125, 49.25])))
    assert (
        module().main(
            ["--source", "hrrr", "--case", str(case), "--cache-dir", str(tmp_path / "cache")]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    assert plan["network_requests_made"] == 0
    assert plan["limits"]["requests"] == 128
    assert plan["request"]["variables"] == ["temperature", "wind"]
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("source", ["hrrr", "era5-temperature", "era5-amounts"])
def test_qualification_public_getter_sequence_with_generated_sources(
    tmp_path, monkeypatch, capsys, source
):
    """Validate the procedure's run path offline, including UTC boundary amounts."""
    if source == "hrrr":
        import meteorology.hourly_weather.native_day as native_day
        import meteorology.hourly_weather.native_source as native
        from .test_request_transport import hrrr_http_fixture

        provider, bbox = hrrr_http_fixture(tmp_path)
        monkeypatch.setattr(native_day, "NativeHTTP", partial(native_day.NativeHTTP, opener=provider))
        monkeypatch.setattr(native, "decode_hour", partial(native.decode_hour, expected_shape=(4, 4)))
    else:
        from . import request_provider
        from .request_fixtures import era5_grib

        bbox = [-125.25, 49, -125, 49.25]
        monkeypatch.setattr(request_provider, "era5_grib", partial(era5_grib, rain=0.002))
        provider = request_provider.install_provider(tmp_path, monkeypatch)
    case_data = dict(date="2024-01-01", bbox=list(bbox))
    if source == "era5-amounts":
        reference = tmp_path / "reference.txt"
        reference.write_text("Generated nonzero guard fixture, never observational validation.")
        case_data["independent_reference"] = dict(
            file=reference.name,
            sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            uri="fixture://generated-only",
            verification_statement="synthetic procedure regression only",
            verified_nonzero_parameters=["tp", "sf"],
            date=case_data["date"],
            bbox=case_data["bbox"],
        )
    case = tmp_path / "case.json"
    case.write_text(json.dumps(case_data))
    assert module().main(
        ["--source", source, "--case", str(case), "--cache-dir", str(tmp_path / "cache"), "--run"]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["independent_regional_accuracy"] == "NOT_RUN"
    assert report["metadata"]["publication_status"] == "COMMITTED"
    assert len(list(tmp_path.glob("*-qualification.parquet"))) == 1
    assert len(provider.calls) == (96 if source == "hrrr" else 9 if source == "era5-amounts" else 5)
