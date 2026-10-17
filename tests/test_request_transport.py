from functools import partial
import json
import re

import pytest

from meteorology import get_temperature, get_weather_request as get_weather, get_wind
from .request_fixtures import era5_grib
from .test_request_api import BBOX


def test_era5_selective_concrete_mocked_http_and_zero_request_final_hit(tmp_path, monkeypatch):
    from meteorology.era5.transport import CDSHTTPProvider, API
    import meteorology.era5.transport as transport
    from .test_era5_transport import Opener, Response, HOST, CATALOGUE

    source = era5_grib(tmp_path / "source.grib", parameters=("t2m",))
    opener = Opener(
        Response(CATALOGUE),
        Response({"licences": [{"id": "cc-by", "revision": 1}]}),
        Response({"id": "reanalysis-era5-single-levels"}),
        Response({"links": [{"rel": "monitor", "href": API + "/retrieve/v1/jobs/fixture"}]}, 201),
        Response({"jobID": "fixture", "status": "successful"}),
        Response(
            {
                "asset": {
                    "value": {"href": f"https://{HOST}/fixture", "file:size": source.stat().st_size}
                }
            }
        ),
        Response(source.read_bytes()),
    )
    monkeypatch.setattr(transport, "CDSHTTPProvider", partial(CDSHTTPProvider, opener=opener))
    monkeypatch.setenv("CDSAPI_KEY", "SYNTHETIC_TOKEN")
    cache = tmp_path / "provider-test-cache"
    result = get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", cache_dir=cache)
    assert result.to_pandas().VALUE.tolist() == pytest.approx([10] * 4, abs=1e-3)
    assert len(opener.calls) == 7
    # Official request submission sees only the temperature dependency.
    requests = [json.loads(request.data) for request, _ in opener.calls if request.data]
    assert any(value.get("inputs", {}).get("variable") == ["2m_temperature"] for value in requests)
    monkeypatch.delenv("CDSAPI_KEY")
    before = len(opener.calls)
    hit = get_temperature("2024-01-01", "2024-01-01", BBOX, "era5", cache_dir=cache)
    assert hit.cache_hit and len(opener.calls) == before
    assert hit.metadata["selection_from_result_id"] == result.metadata["result_id"]


def hrrr_http_fixture(tmp_path, *, h3_output=False):
    ec = pytest.importorskip("eccodes")
    from .test_hrrr_native_source import gribs
    from .test_era5_transport import Response
    from meteorology.hourly_weather.live import FIELDS

    files, bbox = gribs(tmp_path, grid_wind=True)
    midlon = (bbox[0] + bbox[2]) / 2
    midlat = (bbox[1] + bbox[3]) / 2
    bbox = (midlon - 0.03, midlat - 0.03, midlon + 0.03, midlat + 0.03)
    if h3_output:
        import h3

        lat, lon = h3.cell_to_latlng(h3.latlng_to_cell(midlat, midlon, 5))
        for path in files.values():
            with path.open("rb") as stream:
                message = ec.codes_grib_new_from_file(stream)
            try:
                ec.codes_set(message, "latitudeOfFirstGridPointInDegrees", lat - 0.04)
                ec.codes_set(message, "longitudeOfFirstGridPointInDegrees", (lon - 0.08) % 360)
                with path.open("wb") as stream:
                    ec.codes_write(message, stream)
            finally:
                ec.codes_release(message)
        bbox = (lon - 0.001, lat - 0.001, lon + 0.001, lat + 0.001)
    payloads = {}
    indices = {}
    for hour in range(24):
        chunks = []
        lines = []
        offset = 0
        for name in ("temperature_2m_k", "u_wind_10m_ms", "v_wind_10m_ms"):
            with files[name].open("rb") as source:
                message = ec.codes_grib_new_from_file(source)
            try:
                ec.codes_set(message, "dataTime", hour * 100)
                chunk = ec.codes_get_message(message)
            finally:
                ec.codes_release(message)
            lines.append(f"{len(lines) + 1}:{offset}:d=20240101{hour:02}{FIELDS[name]}anl:")
            chunks.append(chunk)
            offset += len(chunk)
        lines.append(f"4:{offset}:d=20240101{hour:02}:END:surface:anl:")
        payloads[hour] = b"".join(chunks)
        indices[hour] = ("\n".join(lines) + "\n").encode()

    class HTTP:
        def __init__(self):
            self.calls = []

        def open(self, request, **kwargs):
            self.calls.append(request)
            hour = int(re.search(r"hrrr.t(\d+)z", request.full_url)[1])
            if request.full_url.endswith(".idx"):
                response = Response(indices[hour])
            else:
                left, right = map(
                    int, re.fullmatch(r"bytes=(\d+)-(\d+)", request.get_header("Range")).groups()
                )
                response = Response(payloads[hour][left : right + 1], 206)
                response.headers["Content-Range"] = f"bytes {left}-{right}/{len(payloads[hour])}"
            response.headers["ETag"] = '"' + "a" * 32 + '"'
            return response

    return HTTP(), bbox


def test_hrrr_selective_bytes_cross_getter_source_reuse_and_vector_rotation(tmp_path, monkeypatch):
    import meteorology.hourly_weather.native_day as native_day
    import meteorology.hourly_weather.native_source as native

    opener, bbox = hrrr_http_fixture(tmp_path)
    monkeypatch.setattr(native_day, "NativeHTTP", partial(native_day.NativeHTTP, opener=opener))
    monkeypatch.setattr(native, "decode_hour", partial(native.decode_hour, expected_shape=(4, 4)))
    options = dict(cache_dir=tmp_path / "mock-provider-cache", spatial_output="native")
    temperature = get_temperature("2024-01-01", "2024-01-01", bbox, "hrrr", **options)
    assert temperature.to_pandas().VALUE.tolist() == pytest.approx(
        [10] * len(temperature.to_pandas()), abs=1e-3
    )
    assert len(opener.calls) == 48  # 24 inventories + only 24 temperature ranges
    original = len(opener.calls)
    assert get_temperature("2024-01-01", "2024-01-01", bbox, "hrrr", **options).cache_hit
    assert len(opener.calls) == original
    wind = get_wind("2024-01-01", "2024-01-01", bbox, "hrrr", **options).to_pandas()
    assert len(opener.calls) == 96  # Reuses all 24 inventories; adds only U/V
    assert wind[wind.METRIC == "WIND_SPEED_10M_MS"].VALUE.tolist() == pytest.approx(
        [5] * sum(wind.METRIC == "WIND_SPEED_10M_MS")
    )
    assert not all(abs(v - 3) < 0.001 for v in wind[wind.METRIC == "U_WIND_10M_MS"].VALUE)
    before = len(opener.calls)
    combined = get_weather(
        ["wind", "temperature"], "2024-01-01", "2024-01-01", bbox, "hrrr", **options
    )
    assert len(opener.calls) == before
    assert set(combined.to_pandas().METRIC) == {
        "TEMPERATURE_2M_C",
        "U_WIND_10M_MS",
        "V_WIND_10M_MS",
        "WIND_SPEED_10M_MS",
    }
    assert len(combined.metadata["request"]["inputs"]) == 3
    assert get_wind("2024-01-01", "2024-01-01", bbox, "hrrr", **options).cache_hit


def test_default_hrrr_h3_getter_preserves_sampling_and_native_identity(tmp_path, monkeypatch):
    import meteorology.hourly_weather.native_day as native_day
    import meteorology.hourly_weather.native_source as native

    opener, bbox = hrrr_http_fixture(tmp_path, h3_output=True)
    monkeypatch.setattr(native_day, "NativeHTTP", partial(native_day.NativeHTTP, opener=opener))
    monkeypatch.setattr(native, "decode_hour", partial(native.decode_hour, expected_shape=(4, 4)))
    result = get_temperature(
        "2024-01-01", "2024-01-01", bbox, "hrrr", cache_dir=tmp_path / "mock-cache"
    )
    data = result.to_pandas()
    assert len(data) == 1 and data.VALUE.iloc[0] == pytest.approx(10, abs=1e-3)
    assert data.H3_INDEX.notna().all() and set(data.H3_RESOLUTION) == {5}
    assert data.SOURCE_GRID_HASH.notna().all() and data.SOURCE_GRID_INDEX.notna().all()
    assert (data.SOURCE_GRID_DISTANCE_M <= 5000).all()
    assert len(opener.calls) == 48
