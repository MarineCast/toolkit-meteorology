import json
import pandas as pd
import pytest
from meteorology import get_temperature, get_weather
from .request_fixtures import era5_grib
from .test_request_api import BBOX, RETRIEVED


def options(tmp_path, **kwargs):
    path = era5_grib(tmp_path / "generated.grib", **kwargs)
    return dict(source_files=[path], retrieved_at_utc=RETRIEVED, cache_dir=tmp_path / "cache")


def test_hourly_preliminary_does_not_contaminate_day(tmp_path):
    opts = options(tmp_path, preliminary_hours=(3,))
    hourly = get_temperature(
        "2024-01-01", "2024-01-01", BBOX, "era5", frequency="hourly", **opts
    ).to_pandas()
    preliminary = hourly[hourly.CONSOLIDATION_STATUS == "contains_preliminary_ERA5T"]
    assert len(preliminary) == 4
    assert set(preliminary.VALID_TIME_UTC) == {"2024-01-01T03:00:00+00:00"}
    daily = get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", **opts).to_pandas()
    assert set(daily.CONSOLIDATION_STATUS) == {"contains_preliminary_ERA5T"}


def test_parameter_status_and_derived_dependencies(tmp_path):
    files = [
        era5_grib(tmp_path / "final.grib", parameters=("t2m", "u10"), expver=1),
        era5_grib(tmp_path / "preliminary.grib", parameters=("tcc", "d2m", "v10"), expver=5),
    ]
    result = get_weather(
        ["temperature", "cloud_cover", "relative_humidity", "wind"],
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        frequency="hourly",
        source_files=files,
        retrieved_at_utc=RETRIEVED,
        cache_dir=tmp_path / "cache",
    )
    data = result.to_pandas()
    assert set(data[data.METRIC == "TEMPERATURE_2M_C"].CONSOLIDATION_STATUS) == {"final"}
    assert set(data[data.METRIC == "TOTAL_CLOUD_COVER_PCT"].CONSOLIDATION_STATUS) == {
        "contains_preliminary_ERA5T"
    }
    for metric, params in [
        ("WIND_SPEED_10M_MS", {"u10", "v10"}),
        ("RELATIVE_HUMIDITY_2M_PCT", {"t2m", "d2m"}),
    ]:
        row = data[data.METRIC == metric].iloc[0]
        assert {d["parameter"] for d in json.loads(row.SOURCE_DEPENDENCIES)} == params
        assert row.CONSOLIDATION_STATUS == "contains_preliminary_ERA5T"
    result.to_parquet(tmp_path / "export.parquet")
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "export.parquet"), data)
    subset = get_temperature(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        frequency="hourly",
        source_files=files,
        retrieved_at_utc=RETRIEVED,
        cache_dir=tmp_path / "cache",
    )
    assert subset.cache_hit
    assert {d["parameter"] for d in subset.metadata["source_forecast_support"]} == {"t2m"}


def test_bitmap_null_and_missing_message_have_distinct_evidence(tmp_path):
    opts = options(tmp_path, missing_hour=3, missing_messages=((4, "t2m"),))
    frame = get_temperature(
        "2024-01-01", "2024-01-01", BBOX, "era5", frequency="hourly", **opts
    ).to_pandas()
    bitmap = frame[frame.VALID_TIME_UTC == "2024-01-01T03:00:00+00:00"].iloc[0]
    missing = frame[frame.VALID_TIME_UTC == "2024-01-01T04:00:00+00:00"].iloc[0]
    assert bitmap.SOURCE_VALUE_STATUS == "BITMAP_NULL"
    assert json.loads(bitmap.SOURCE_SHA256_RECEIPTS)
    assert missing.SOURCE_VALUE_STATUS == "MISSING_MESSAGE"
    assert json.loads(missing.SOURCE_SHA256_RECEIPTS) == []
    assert pd.isna(missing.RETRIEVED_AT_UTC)


@pytest.mark.parametrize(
    "preliminary_hour,expected", [(0, "final"), (24, "contains_preliminary_ERA5T")]
)
def test_accumulation_boundary_lineage_uses_only_contributing_intervals(
    tmp_path, preliminary_hour, expected
):
    opts = options(tmp_path, parameters=("sf", "tp"), preliminary_hours=(preliminary_hour,))
    result = get_weather(
        ["snowfall", "precipitation"], "2024-01-01", "2024-01-01", BBOX, "era5", **opts
    )
    data = result.to_pandas()
    assert set(data.CONSOLIDATION_STATUS) == {expected}
    evidence = json.loads(data.iloc[0].SOURCE_DEPENDENCIES)
    assert len(evidence) == 24
    assert {d["valid_time_utc"] for d in evidence} == set(
        pd.date_range("2024-01-01T01:00Z", periods=24, freq="h").map(lambda t: t.isoformat())
    )
    assert len(result.metadata["source_forecast_support"]) == 48


def test_each_hour_preserves_its_receipt_and_retrieval_time(tmp_path, monkeypatch):
    from meteorology.era5.source import decode_grib

    files = [era5_grib(tmp_path / f"{hour}.grib", hours=(hour,)) for hour in range(24)]

    def timestamps(*a, **kw):
        for frame in decode_grib(*a, **kw):
            hour = pd.Timestamp(frame.VALID_TIME_UTC.iloc[0]).hour
            frame["RETRIEVED_AT_UTC"] = (
                pd.Timestamp(RETRIEVED) + pd.Timedelta(hours=hour)
            ).isoformat()
            yield frame

    monkeypatch.setattr("meteorology.era5.source.decode_grib", timestamps)
    result = get_temperature(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        frequency="hourly",
        source_files=files,
        retrieved_at_utc=RETRIEVED,
        cache_dir=tmp_path / "cache",
    )
    data = result.to_pandas()
    assert data.RETRIEVED_AT_UTC.nunique() == 24
    assert data.SOURCE_SHA256_RECEIPTS.nunique() == 24
    assert all(len(json.loads(value)) == 1 for value in data.SOURCE_SHA256_RECEIPTS)
    for row in data.itertuples():
        dependency = json.loads(row.SOURCE_DEPENDENCIES)[0]
        assert dependency["retrieved_at_utc"] == row.RETRIEVED_AT_UTC
        assert dependency["valid_time_utc"] == row.VALID_TIME_UTC


def test_legacy_lineage_is_pin_only_and_retains_contract_identity(tmp_path):
    import pyarrow.parquet as pq
    from meteorology.request import digest
    from meteorology.era5.resources import atomic_json
    from meteorology.artifacts import checksum_path

    opts = options(tmp_path, preliminary_hours=(3,))
    result = get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", frequency="hourly", **opts)
    manifest = json.loads((result.directory / "MANIFEST.json").read_text())
    artifact = manifest["artifacts"][0]
    path = result.directory / artifact["path"]
    table = pq.read_table(path).drop(["SOURCE_VALUE_STATUS", "SOURCE_DEPENDENCIES"])
    pq.write_table(table, path)
    artifact.update(bytes=path.stat().st_size, sha256=checksum_path(path))
    manifest["request"]["method"] = "meteorology-selective-utc-request-v1"
    manifest["request"]["schema"] = "meteorology-date-support-metric-v1"
    original_id = manifest["result_id"]
    manifest["result_id"] = digest({"legacy_result": original_id})
    legacy_dir = result.directory.with_name(manifest["result_id"])
    result.directory.rename(legacy_dir)
    (opts["cache_dir"] / "fixtures/ACTIVE_GENERATIONS.json").unlink()
    manifest.pop("source_generation")
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = digest(manifest)
    atomic_json(legacy_dir / "MANIFEST.json", manifest)
    pinned = get_temperature(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        frequency="hourly",
        result_id=manifest["result_id"],
        **opts,
    )
    assert pinned.metadata["request"]["schema"].endswith("v1")
    assert pinned.to_pandas().SOURCE_DEPENDENCIES.isna().all()
    offline_pin = get_temperature(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        "era5",
        frequency="hourly",
        cache_dir=opts["cache_dir"],
        result_id=manifest["result_id"],
    )
    assert offline_pin.metadata["request"]["evidence_kind"] == "synthetic_fixture"
    current = get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", frequency="hourly", **opts)
    assert not current.cache_hit
    assert current.metadata["request"]["schema"].endswith("v2")
