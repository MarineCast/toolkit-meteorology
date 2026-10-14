from meteorology.era5.resources import Limits
from .request_provider import install_provider
from .test_request_review import temperature


def test_warm_era5_source_reaggregation_under_one_request_cap(tmp_path, monkeypatch):
    provider = install_provider(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    temperature(cache, frequency="hourly")
    before = len(provider.calls)
    daily = temperature(cache, limits=Limits(memory_bytes=1024**3, requests=1))
    assert len(provider.calls) == before
    assert daily.metadata["budget"]["requests"] == 0



def test_hrrr_partial_cache_counts_only_missing_dependencies(tmp_path,monkeypatch):
    from functools import partial
    from meteorology import get_temperature,get_wind,get_weather
    from .test_request_transport import hrrr_http_fixture
    import meteorology.hourly_weather.native_day as nd
    import meteorology.hourly_weather.native_source as ns
    opener,bbox=hrrr_http_fixture(tmp_path)
    monkeypatch.setattr(nd,'NativeHTTP',partial(nd.NativeHTTP,opener=opener))
    monkeypatch.setattr(ns,'decode_hour',partial(ns.decode_hour,expected_shape=(4,4)))
    opts=dict(cache_dir=tmp_path/'cache',spatial_output='native')
    get_temperature('2024-01-01','2024-01-01',bbox,'hrrr',frequency='hourly',**opts)
    # cached inspection constructs no provider transport even if patched test factory supplies an opener
    plan=get_wind('2024-01-01','2024-01-01',bbox,'hrrr',dry_run=True,**opts)
    assert len(plan['cache']['reusable_source_objects'])==24
    assert len(plan['cache']['missing_source_work'])==48
    assert plan['cache']['remaining_provider_requests_lower_bound']==48
    before=len(opener.calls)
    get_wind('2024-01-01','2024-01-01',bbox,'hrrr',limits=Limits(memory_bytes=1024**3,requests=48),**opts)
    assert len(opener.calls)-before==48
    before=len(opener.calls)
    combined=get_weather(['temperature','wind'],'2024-01-01','2024-01-01',bbox,'hrrr',
                         limits=Limits(memory_bytes=1024**3,requests=1),**opts)
    assert len(opener.calls)==before
    assert combined.metadata['budget']['requests']==0


def test_era5_source_preview_and_credentials_free_processing(tmp_path,monkeypatch):
    provider=install_provider(tmp_path,monkeypatch)
    cache=tmp_path/'cache'
    cold=temperature(cache,dry_run=True)
    assert cold['cache']['missing_source_work']
    assert cold['cache']['uncertainties']
    temperature(cache,frequency='hourly')
    monkeypatch.delenv('CDSAPI_KEY')
    def forbidden(**kwargs):
        raise AssertionError('Provider must not be constructed for retained bytes')
    monkeypatch.setattr('meteorology.era5.transport.CDSHTTPProvider',forbidden)
    warm=temperature(cache,dry_run=True)
    assert not warm['cache']['final_result_hit']
    assert len(warm['cache']['reusable_source_objects'])==1
    assert not warm['cache']['missing_source_work']
    assert warm['cache']['remaining_provider_requests_lower_bound']==0
    result=temperature(cache,limits=Limits(memory_bytes=1024**3,requests=1))
    assert result.metadata['budget']['requests']==0
    assert temperature(cache,dry_run=True)['cache']['final_result_hit']
    assert len(provider.calls)==5


def test_corrupt_retained_source_rejected_in_preflight(tmp_path,monkeypatch):
    import pytest
    provider=install_provider(tmp_path,monkeypatch)
    cache=tmp_path/'cache'
    temperature(cache,frequency='hourly')
    source=next((cache/'real/sources/era5').glob('*/source.grib'))
    source.write_bytes(b'CORRUPT')
    calls=len(provider.calls)
    with pytest.raises(ValueError,match='corrupt'):
        temperature(cache,dry_run=True)
    with pytest.raises(ValueError,match='corrupt'):
        temperature(cache,limits=Limits(memory_bytes=1024**3,requests=1))
    assert len(provider.calls)==calls


def test_era5_does_not_reuse_a_different_job_selection(tmp_path,monkeypatch):
    from meteorology import get_weather
    provider=install_provider(tmp_path,monkeypatch)
    cache=tmp_path/'cache'
    temperature(cache,frequency='hourly')
    preview=get_weather(['temperature','cloud_cover'],'2024-01-01','2024-01-01',
                        (-125.25,49,-125,49.25),'era5',cache_dir=cache,dry_run=True)
    assert not preview['cache']['reusable_source_objects']
    assert preview['cache']['missing_source_work']
    assert len(provider.calls)==5


def test_interrupted_request_budget_remains_charged(tmp_path,monkeypatch):
    import pytest
    provider=install_provider(tmp_path,monkeypatch)
    cache=tmp_path/'cache'
    provider.fail=True
    caps=Limits(memory_bytes=1024**3,requests=2)
    with pytest.raises(RuntimeError):
        temperature(cache,limits=caps)
    provider.fail=False
    before=len(provider.calls)
    with pytest.raises(ValueError,match='request cap'):
        temperature(cache,limits=caps)
    assert len(provider.calls)==before
