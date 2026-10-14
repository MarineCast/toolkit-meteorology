from functools import partial
import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from meteorology import (
    get_weather,
    get_temperature,
    get_precip,
    get_precipitation,
    get_snowfall,
    get_wind,
    WeatherResult,
)
from meteorology.era5.resources import Limits
from .request_fixtures import era5_grib

BBOX = (-125.25, 49.0, -125.0, 49.25)
RETRIEVED = "2025-01-01T00:00:00Z"


def fixture_options(tmp_path, **kwargs):
    path = era5_grib(tmp_path / "generated.grib", **kwargs)
    return dict(source_files=[path], retrieved_at_utc=RETRIEVED, cache_dir=tmp_path / "cache")


def test_era5_temperature_public_api_durable_metadata_and_subset(tmp_path):
    options = fixture_options(tmp_path, parameters=("t2m", "u10", "v10"))
    result = get_weather(
        ["temperature", "wind"], "2024-01-01", "2024-01-01", BBOX, "era5", **options
    )
    assert isinstance(result, WeatherResult)
    data = result.to_pandas()
    assert set(data.METRIC) == {
        "TEMPERATURE_2M_C",
        "U_WIND_10M_MS",
        "V_WIND_10M_MS",
        "WIND_SPEED_10M_MS",
    }
    assert len(data) == 4 * 4 and not data.duplicated(["DATE", "SOURCE_GRID_INDEX", "METRIC"]).any()
    assert data[data.METRIC == "TEMPERATURE_2M_C"].VALUE.tolist() == pytest.approx(
        [10] * 4, abs=1e-3
    )
    assert data[data.METRIC == "WIND_SPEED_10M_MS"].VALUE.tolist() == pytest.approx([5] * 4)
    result.to_parquet(tmp_path / "export.parquet")
    embedded = json.loads(
        pq.ParquetFile(tmp_path / "export.parquet").schema_arrow.metadata[b"meteorology.manifest"]
    )
    assert embedded["request"]["timezone"] == "UTC"
    assert embedded["inputs"] and embedded["source_grids"]
    assert embedded["software_version"] and embedded["scientific_runtime"]["eccodes"]
    assert embedded["source_forecast_support"]
    assert all(h["forecast_hour"] == 0 for h in embedded["source_forecast_support"])
    assert embedded["request"]["evidence_kind"] == "synthetic_fixture"
    assert embedded["production_shared_study_release"] is False
    subset = get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", **options)
    assert subset.cache_hit and len(subset.to_pandas()) == 4
    assert len(subset.metadata["artifacts"]) == 1
    assert set(subset.metadata["metrics"]) == {"TEMPERATURE_2M_C"}
    reordered = get_weather(
        ["wind", "temperature", "wind"], "2024-01-01", "2024-01-01", BBOX, "era5", **options
    )
    assert reordered.cache_hit and reordered.metadata["result_id"] == result.metadata["result_id"]


@pytest.mark.parametrize("day", ["2023-12-31", "2024-02-29", "2024-01-31"])
def test_precipitation_snow_and_boundaries(tmp_path, day):
    options = fixture_options(tmp_path, day=day, parameters=("tp", "sf"))
    result = get_weather(["precipitation", "snowfall"], day, day, BBOX, "era5", **options)
    data = result.to_pandas()
    assert data[data.METRIC == "PRECIPITATION_MM"].VALUE.tolist() == [0.0] * 4
    assert data[data.METRIC == "SNOWFALL_WATER_EQUIVALENT_MM"].VALUE.tolist() == pytest.approx(
        [24] * 4
    )
    assert set(data[data.METRIC == "SNOWFALL_WATER_EQUIVALENT_MM"].UNITS) == {"mm water equivalent"}
    assert set(data.INTERVAL_END_UTC) == {
        (pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1)).isoformat()
    }
    assert any(
        h["forecast_hour"] == 1 and h["parameter"] == "sf"
        for h in result.metadata["source_forecast_support"]
    )
    assert get_precip is get_precipitation
    assert get_snowfall(day, day, BBOX, "era5", **options).cache_hit


def test_missing_precipitation_is_null_and_not_successful_cache_hit(tmp_path):
    options = fixture_options(tmp_path, parameters=("tp",), missing_hour=24)
    result = get_precip("2024-01-01", "2024-01-01", BBOX, "era5", **options)
    data = result.to_pandas()
    assert data.VALUE.isna().all() and set(data.STATUS) == {"PARTIAL"}
    assert set(data.VALID_HOURS) == {23} and set(data.COVERAGE_FRACTION) == {23 / 24}
    assert result.metadata["status"] == "PARTIAL"
    assert not get_precip("2024-01-01", "2024-01-01", BBOX, "era5", **options).cache_hit


@pytest.mark.parametrize(
    "source,variable",
    [
        ("hrrr", "precipitation"),
        ("hrrr", "snowfall"),
        ("hrrr", "dewpoint"),
        ("era5", "visibility"),
        ("era5", "unknown"),
    ],
)
def test_unsupported_before_provider(tmp_path, source, variable):
    with pytest.raises(ValueError, match="no qualified"):
        get_weather(
            [variable], "2024-01-01", "2024-01-01", BBOX, source, cache_dir=tmp_path / "cache"
        )
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize(
    "options",
    [
        {"start_date": "2024-01-02", "end_date": "2024-01-01"},
        {"bbox": (0, 0, 1, float("nan"))},
        {"bbox": (170, -5, -170, 5)},
        {"start_date": "2010-01-01", "end_date": "2010-01-01"},
        {"limits": Limits(requests=0)},
        {"limits": Limits(memory_bytes=1024)},
        {"limits": Limits(staging_bytes=1)},
        {"spatial_output": "h3", "h3_resolution": 6},
        {"data_source": "automatic"},
    ],
)
def test_invalid_before_provider(tmp_path, options):
    arguments = dict(
        start_date="2024-01-01",
        end_date="2024-01-01",
        bbox=BBOX,
        data_source="hrrr",
        cache_dir=tmp_path / "cache",
    )
    arguments.update(options)
    with pytest.raises(ValueError):
        get_temperature(**arguments)
    assert not (tmp_path / "cache").exists()


def test_era5_unqualified_h3_rejected():
    with pytest.raises(ValueError, match="unqualified"):
        get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", spatial_output="h3", dry_run=True)


def test_cache_corruption_fails_without_provider(tmp_path):
    options = fixture_options(tmp_path)
    result = get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", **options)
    artifact = result.directory / result.metadata["artifacts"][0]["path"]
    artifact.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", **options)


def test_producer_change_invalidates_derived_cache(tmp_path, monkeypatch):
    import meteorology.request as request

    options = fixture_options(tmp_path)
    first = get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", **options)
    monkeypatch.setattr(request, "fingerprint", lambda: "changed-method")
    second = get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", **options)
    assert not second.cache_hit and first.metadata["result_id"] != second.metadata["result_id"]
    assert (first.directory / "MANIFEST.json").is_file()


def test_cli_python_parity_and_config_free_help(tmp_path, monkeypatch, capsys):
    from meteorology.cli import main

    options = fixture_options(tmp_path)
    monkeypatch.setenv("MARINECAST_STUDY_CONFIG", str(tmp_path / "missing-study.json"))
    monkeypatch.setenv("METEOROLOGY_WORKSPACE", str(tmp_path / "missing-workspace"))
    for args in [
        ["get", "--help"],
        ["get", "temperature", "--help"],
        ["build", "surface-weather", "--help"],
    ]:
        with pytest.raises(SystemExit) as exc:
            main(args)
        assert exc.value.code == 0
    assert main(["sources"]) == 0
    assert main(["variables", "--data-source", "era5"]) == 0
    output = tmp_path / "cli.parquet"
    assert (
        main(
            [
                "get",
                "temperature",
                "--start-date",
                "2024-01-01",
                "--end-date",
                "2024-01-01",
                "--bbox",
                *map(str, BBOX),
                "--data-source",
                "era5",
                "--source-files",
                str(options["source_files"][0]),
                "--retrieved-at-utc",
                RETRIEVED,
                "--cache-dir",
                str(options["cache_dir"]),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    expected = get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", **options).to_pandas()
    pd.testing.assert_frame_equal(pq.read_table(output).to_pandas(), expected)
    assert (
        main(
            [
                "get",
                "temperature",
                "--start-date",
                "2024-01-01",
                "--end-date",
                "2024-01-01",
                "--bbox",
                *map(str, BBOX),
                "--data-source",
                "era5",
                "--dry-run",
            ]
        )
        == 0
    )
    assert "required_inputs" in capsys.readouterr().out


def test_hrrr_temperature_through_actual_selective_decoder(tmp_path, monkeypatch):
    from .test_hrrr_native_source import gribs
    import meteorology.hourly_weather.native_source as native
    import eccodes as ec

    files, bbox = gribs(tmp_path)
    midlat = (bbox[1] + bbox[3]) / 2
    midlon = (bbox[0] + bbox[2]) / 2
    bbox = (midlon - 0.03, midlat - 0.03, midlon + 0.03, midlat + 0.03)
    sources = {}
    for hour in range(24):
        valid = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=hour)
        path = tmp_path / f"temperature-{hour}.grib"
        with files["temperature_2m_k"].open("rb") as stream:
            message = ec.codes_grib_new_from_file(stream)
        try:
            ec.codes_set(message, "dataTime", hour * 100)
            with path.open("wb") as stream:
                ec.codes_write(message, stream)
        finally:
            ec.codes_release(message)
        sources[valid.isoformat()] = {"temperature_2m_k": path}
    monkeypatch.setattr(native, "decode_hour", partial(native.decode_hour, expected_shape=(4, 4)))
    result = get_temperature(
        "2024-01-01",
        "2024-01-01",
        bbox,
        "hrrr",
        spatial_output="native",
        source_files=sources,
        retrieved_at_utc=RETRIEVED,
        cache_dir=tmp_path / "cache",
    )
    data = result.to_pandas()
    assert np.allclose(data.VALUE, 10) and set(data.UNITS) == {"degC"}
    assert set(data.STATUS) == {"COMPLETE"} and data.H3_INDEX.isna().all()
    assert result.metadata["request"]["inputs"] == ["temperature_2m_k"]


def test_daily_mean_hourly_wind_magnitude(tmp_path):
    options = fixture_options(tmp_path, parameters=("u10", "v10"))
    wind = get_wind("2024-01-01", "2024-01-01", BBOX, "era5", **options).to_pandas()
    assert wind[wind.METRIC == "WIND_SPEED_10M_MS"].VALUE.tolist() == pytest.approx([5] * 4)
    assert set(wind.WIND_VECTOR_BASIS) == {"earth_relative"}


def test_concurrent_writers_share_one_execution(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import meteorology.request_adapters as adapters

    options = fixture_options(tmp_path)
    original = adapters.era5_day
    executions = []

    def counted(*args, **kwargs):
        executions.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(adapters, "era5_day", counted)

    def request():
        return get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", **options)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: request(), range(2)))
    assert executions == ["2024-01-01"]
    assert sum(r.cache_hit for r in (a, b)) == 1
    assert a.metadata["result_id"] == b.metadata["result_id"]
    assert len(list((options["cache_dir"] / "fixtures/results").glob("*/MANIFEST.json"))) == 1


def test_multi_day_subset_only_reads_requested_date_and_metric(tmp_path, monkeypatch):
    from meteorology.era5.source import decode_grib

    dates = ["2024-02-28", "2024-02-29"]
    files = {
        day: [era5_grib(tmp_path / f"{day}.grib", day=day, parameters=("t2m", "tcc"))]
        for day in dates
    }
    options = dict(source_files=files, retrieved_at_utc=RETRIEVED, cache_dir=tmp_path / "cache")
    result = get_weather(
        ["temperature", "cloud_cover"], dates[0], dates[-1], BBOX, "era5", **options
    )
    decoded = []
    original = decode_grib

    # A final result hit does not normalize again.
    def forbidden(*args, **kwargs):
        decoded.append(args[0])
        yield from original(*args, **kwargs)

    monkeypatch.setattr("meteorology.era5.source.decode_grib", forbidden)
    subset = get_temperature(dates[-1], dates[-1], BBOX, "era5", **options)
    assert subset.cache_hit and not decoded
    assert len(subset.metadata["artifacts"]) == 1
    assert subset.metadata["artifacts"][0]["day"] == "2024-02-29"
    assert set(subset.to_pandas().DATE) == {"2024-02-29"}
    assert len(result.to_pandas()) == 16
    # Exporting over another retained cache generation is forbidden.
    with pytest.raises(ValueError, match="immutable"):
        subset.to_parquet(result.directory / "different.parquet")


def test_hourly_accumulation_slots_and_nulls(tmp_path):
    options = fixture_options(tmp_path, parameters=("tp", "sf"), missing_hour=24)
    result = get_weather(
        ["precipitation", "snowfall"],
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        frequency="hourly",
        **options,
    )
    data = result.to_pandas()
    assert len(data) == 4 * 24 * 2
    assert not data.duplicated(["SOURCE_GRID_INDEX", "VALID_TIME_UTC", "METRIC"]).any()
    assert set(data.VALID_TIME_UTC) == set(
        pd.date_range("2024-01-01T01:00:00Z", periods=24, freq="h").map(lambda t: t.isoformat())
    )
    assert len(data[data.STATUS == "UNAVAILABLE"]) == 8
    assert data[data.STATUS == "UNAVAILABLE"].VALUE.isna().all()


def test_unexpected_grid_and_source_corruption_are_rejected(tmp_path):
    options = fixture_options(tmp_path, area=(49.5, -126, 48.75, -125.25))
    with pytest.raises(ValueError, match="planned native rectangle"):
        get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", **options)
    assert not list((options["cache_dir"] / "fixtures/results").glob("*/MANIFEST.json"))


def test_final_missing_value_does_not_borrow_preliminary(tmp_path):
    final = era5_grib(tmp_path / "final.grib", parameters=("t2m",), expver=1, missing_hour=3)
    preliminary = era5_grib(tmp_path / "preliminary.grib", parameters=("t2m",), expver=5)
    result = get_temperature(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        source_files=[preliminary, final],
        retrieved_at_utc=RETRIEVED,
        cache_dir=tmp_path / "cache",
    )
    assert result.to_pandas().VALUE.isna().all()
    assert set(result.to_pandas().VALID_HOURS) == {23}


def test_era5_all_instant_quantities_have_correct_units_and_reductions(tmp_path):
    options = fixture_options(tmp_path, parameters=("t2m", "d2m", "u10", "v10", "msl", "tcc"))
    result = get_weather(
        ["temperature", "dewpoint", "relative_humidity", "wind", "pressure", "cloud_cover"],
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        **options,
    )
    data = result.to_pandas()
    expected = {
        "DEWPOINT_2M_C": (5.0, "degC"),
        "MEAN_SEA_LEVEL_PRESSURE_HPA": (1013.25, "hPa"),
        "TOTAL_CLOUD_COVER_PCT": (50.0, "percent"),
        "RELATIVE_HUMIDITY_2M_PCT": (
            100 * np.exp(17.67 * 5 / (5 + 243.5) - 17.67 * 10 / (10 + 243.5)),
            "percent",
        ),
    }
    for metric, (value, unit) in expected.items():
        rows = data[data.METRIC == metric]
        assert rows.VALUE.tolist() == pytest.approx([value] * 4, abs=0.001)
        assert set(rows.UNITS) == {unit} and set(rows.REDUCTION) == {"mean"}
    assert set(result.metadata["request"]["inputs"]) == {"t2m", "d2m", "u10", "v10", "msl", "tcc"}
