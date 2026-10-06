import io,json
from pathlib import Path
from urllib.error import HTTPError

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from meteorology.hourly_weather.native_day import (exact_plan,run_day,ArchiveError,NativeHTTP)
from meteorology.hourly_weather.live import FIELDS
from meteorology.hourly_weather.native_source import validate_hour
from meteorology.era5.resources import Budget,Limits,LimitExceeded

STUDY=Path(__file__).parent/'fixtures/study.coastal-policy.v1.json'


class Response(io.BytesIO):
    def __init__(self,data,*,status=200,headers=None):
        super().__init__(data);self.status=status
        self.headers={'Content-Length':str(len(data)),'ETag':'"'+'a'*32+'"',**(headers or {})}


class Archive:
    def __init__(self,missing=(),interrupt=None,bad_range=False):
        self.missing=set(missing);self.interrupt=interrupt;self.bad_range=bad_range;self.calls=[]
    def open(self,request,timeout):
        self.calls.append(request)
        hour=int(request.full_url.split('hrrr.t')[1][:2])
        if request.full_url.endswith('.idx'):
            if hour in self.missing:raise HTTPError(request.full_url,404,'missing',{},io.BytesIO())
            lines=[f'{i+1}:{i*8}:d=20240101{hour:02}{selector}anl:' for i,selector in enumerate(FIELDS.values())]
            lines.append(f'9:64:d=20240101{hour:02}:IGNORED:surface:anl:')
            return Response(('\n'.join(lines)+'\n').encode())
        start,end=map(int,request.get_header('Range').removeprefix('bytes=').split('-'))
        if self.interrupt==(hour,start//8):
            self.interrupt=None
            class Interrupted(Response):
                def read(self,n):raise ConnectionResetError('SYNTHETIC INTERRUPTION')
            return Interrupted(b'GRIBtest',status=206,headers={'Content-Range':f'bytes {start}-{end}/72'})
        return Response(b'GRIBtest',status=206,headers={'Content-Range':f'bytes {start+1 if self.bad_range else start}-{end}/72'})


def decoded(files,*,valid,bbox,halo,budget):
    hour=valid.hour;n=4
    frame=pd.DataFrame(dict(SOURCE_GRID_INDEX=[1000,1001,1002,1003],SOURCE_LAT=[49.1,49.1,49.2,49.2],
        SOURCE_LON=[-125.3,-125.2,-125.3,-125.2],VALID_TIME_UTC=valid.isoformat(),INIT_TIME_UTC=valid.isoformat(),
        SOURCE_MODEL='HRRR',FORECAST_HOUR=0,SOURCE_GRID_HASH='a'*64,
        RAW_U_WIND_10M_MS=3 if hour<12 else -3,RAW_V_WIND_10M_MS=4 if hour<12 else -4,
        SOURCE_I_BEARING_DEG=90.,SOURCE_J_HANDEDNESS=-1.,
        TEMPERATURE_2M_C=10.+hour/10,RELATIVE_HUMIDITY_2M_PCT=50.,
        U_WIND_10M_MS=3 if hour<12 else -3,V_WIND_10M_MS=4 if hour<12 else -4,
        WIND_GUST_SURFACE_MS=8.,VISIBILITY_KM=5.,TOTAL_CLOUD_COVER_PCT=50.,
        MEAN_SEA_LEVEL_PRESSURE_HPA=1013.25,WIND_SPEED_10M_MS=5.))
    raw=dict(temperature_2m_k=10.+hour/10,relative_humidity_2m_pct=50.,
        u_wind_10m_ms=3 if hour<12 else -3,v_wind_10m_ms=4 if hour<12 else -4,
        wind_gust_surface_ms=8.,visibility_m=5.,total_cloud_cover_pct=.5,mean_sea_level_pressure_pa=1013.25)
    for name,value in raw.items():frame['RAW_SOURCE_'+name]=value
    evidence=dict(source_grid_hash='a'*64,source_wind_basis='earth_relative',source_evidence_kind='synthetic_fixture',
        native_points=1905141,crop_points=n,units=dict(temperature_2m_k='degC',relative_humidity_2m_pct='%',
        u_wind_10m_ms='m/s',v_wind_10m_ms='m/s',wind_gust_surface_ms='m/s',visibility_m='km',
        total_cloud_cover_pct='1',mean_sea_level_pressure_pa='hPa'))
    validate_hour(frame,evidence,valid)
    return frame,evidence


def test_exact_plan_native_caps_and_no_precipitation():
    p=exact_plan(STUDY)
    assert p['nominal_http_requests']==216 and len(p['cycles'])==24
    assert p['limits']['transfer_bytes']==512*1024**2 and p['limits']['staging_bytes']==1024**3
    assert p['no_precipitation_arm'] and p['final_h3_eligible'] is False


def test_complete_day_cache_resume_receipts_units_and_wind(tmp_path):
    p=exact_plan(STUDY);archive=Archive();root=tmp_path/'out'
    path=run_day(STUDY,p,root,opener=archive,decoder=decoded)
    manifest=json.loads(path.read_text());assert len(archive.calls)==216
    assert manifest['actual_hours']==24 and manifest['final_h3_eligible'] is False and manifest['period_complete'] is False
    daily=pq.read_table(root/next(a['path'] for a in manifest['artifacts'] if a['path'].startswith('native-daily'))).to_pandas()
    assert np.allclose(daily.HRRR_TEMPERATURE_2M_C,11.15)
    assert (daily.HRRR_MEAN_SEA_LEVEL_PRESSURE_HPA==1013.25).all()
    assert (daily.HRRR_WIND_SPEED_10M_MS==5).all() and (daily.HRRR_U_WIND_10M_MS==0).all()
    assert daily.HRRR_PRECIPITATION_MM.isna().all() and (daily.HRRR_PRECIPITATION_MM_VALID_HOURS==0).all()
    before={p:str(p.stat().st_mtime_ns) for p in (root/'hours').rglob('*.parquet')}
    assert run_day(STUDY,p,root,opener=archive,decoder=decoded)==path
    assert len(archive.calls)==216
    assert before=={p:str(p.stat().st_mtime_ns) for p in (root/'hours').rglob('*.parquet')}
    assert json.loads((root/'TRANSFER_BUDGET.json').read_text())['requests']==216


def test_missing_hour_partial_then_acquire_only_missing_hour(tmp_path):
    p=exact_plan(STUDY);archive=Archive(missing=[12]);root=tmp_path/'out'
    checkpoint=run_day(STUDY,p,root,opener=archive,decoder=decoded)
    state=json.loads(checkpoint.read_text())
    assert state['status']=='PARTIAL_NATIVE_CHECKPOINT_NOT_PUBLISHED' and state['actual_hours']==23
    assert not (root/'MANIFEST.json').exists()
    daily=pq.read_table(root/next(a['path'] for a in state['artifacts'] if a['path'].startswith('native-daily'))).to_pandas()
    assert daily.HRRR_TEMPERATURE_2M_C.isna().all() and (daily.HRRR_TEMPERATURE_2M_C_VALID_HOURS==23).all()
    assert np.allclose(daily.HRRR_TEMPERATURE_2M_C_COVERAGE_FRACTION,23/24)
    assert len(archive.calls)==208
    archive.missing.clear()
    assert run_day(STUDY,p,root,opener=archive,decoder=decoded).name=='MANIFEST.json'
    assert len(archive.calls)==217
    assert all('hrrr.t12z' in request.full_url for request in archive.calls[208:])


def test_interrupted_range_preserved_and_successful_ranges_reused(tmp_path):
    p=exact_plan(STUDY);archive=Archive(interrupt=(10,2));root=tmp_path/'out'
    with pytest.raises(ConnectionResetError):run_day(STUDY,p,root,opener=archive,decoder=decoded)
    partial=list(root.rglob('*.partial'));assert partial and not (root/'MANIFEST.json').exists()
    count=len(archive.calls)
    assert run_day(STUDY,p,root,opener=archive,decoder=decoded).name=='MANIFEST.json'
    assert len(archive.calls)==217 and len(archive.calls)-count==123
    assert all(path.exists() for path in partial)


def test_cache_corruption_rejected_before_any_new_requests(tmp_path):
    p=exact_plan(STUDY);archive=Archive(missing=[0]);root=tmp_path/'out'
    run_day(STUDY,p,root,opener=archive,decoder=decoded)
    source=next((root/'hours'/'20240101T23Z').glob('temperature_2m_k-a*.grib2'))
    source.write_bytes(b'CORRUPTED')
    count=len(archive.calls);archive.missing.clear()
    with pytest.raises(ArchiveError,match='checksum'):run_day(STUDY,p,root,opener=archive,decoder=decoded)
    assert len(archive.calls)==count


def test_bad_range_never_promoted(tmp_path):
    p=exact_plan(STUDY);archive=Archive(bad_range=True);root=tmp_path/'out'
    with pytest.raises(ArchiveError,match='exact selected range'):run_day(STUDY,p,root,opener=archive,decoder=decoded)
    assert not (root/'MANIFEST.json').exists() and not list(root.rglob('*.grib2'))


def test_bitmap_missing_native_value_is_null_partial_daily(tmp_path):
    def missing(files,**kwargs):
        frame,evidence=decoded(files,**kwargs)
        if kwargs['valid'].hour==5:
            frame.loc[0,['TEMPERATURE_2M_C','RAW_SOURCE_temperature_2m_k']]=np.nan
        return frame,evidence
    p=exact_plan(STUDY);root=tmp_path/'out'
    manifest=json.loads(run_day(STUDY,p,root,opener=Archive(),decoder=missing).read_text())
    daily=pq.read_table(root/next(a['path'] for a in manifest['artifacts'] if a['path'].startswith('native-daily'))).to_pandas()
    row=daily.iloc[0];assert pd.isna(row.HRRR_TEMPERATURE_2M_C) and row.HRRR_TEMPERATURE_2M_C_VALID_HOURS==23
    assert row.HRRR_TEMPERATURE_2M_C_STATUS=='PARTIAL'


def test_wrong_units_fail_independent_qa(tmp_path):
    def wrong(files,**kwargs):
        frame,evidence=decoded(files,**kwargs);evidence['units']['visibility_m']='unknown'
        return frame,evidence
    with pytest.raises(ValueError,match='unit metadata'):
        run_day(STUDY,exact_plan(STUDY),tmp_path/'out',opener=Archive(),decoder=wrong)
    assert not (tmp_path/'out'/'MANIFEST.json').exists()


def test_memory_cap_before_network(tmp_path):
    p=exact_plan(STUDY);root=tmp_path/'out';archive=Archive()
    b=Budget(Limits(**p['limits']),rss=lambda:1024**3+1,staging_root=root)
    with pytest.raises(LimitExceeded,match='Memory'):run_day(STUDY,p,root,opener=archive,decoder=decoded,budget=b)
    assert not archive.calls


def test_changed_exact_plan_before_network(tmp_path):
    p=exact_plan(STUDY);p['day']='2024-01-02';archive=Archive()
    with pytest.raises(ValueError,match='Plan differs'):run_day(STUDY,p,tmp_path/'out',opener=archive,decoder=decoded)
    assert not archive.calls


def test_elapsed_limit_retained_before_later_network(tmp_path):
    p=exact_plan(STUDY);root=tmp_path/'out';archive=Archive();clock=[0]
    b=Budget(Limits(**p['limits']),rss=lambda:0,clock=lambda:clock[0],staging_root=root)
    def slow(files,**kwargs):
        result=decoded(files,**kwargs);clock[0]=3601;return result
    with pytest.raises(LimitExceeded,match='time cap'):
        run_day(STUDY,p,root,opener=archive,decoder=slow,budget=b)
    assert len(archive.calls)==9 and not (root/'MANIFEST.json').exists()
    assert json.loads((root/'TRANSFER_BUDGET.json').read_text())['elapsed_seconds']==3601


def test_s3_generation_mix_fails_without_day_manifest(tmp_path):
    class Mixed(Archive):
        def open(self,request,timeout):
            result=super().open(request,timeout)
            if request.get_header('Range')=='bytes=8-15':result.headers['ETag']='"'+'b'*32+'"'
            return result
    with pytest.raises(ArchiveError,match='different S3 object generations'):
        run_day(STUDY,exact_plan(STUDY),tmp_path/'out',opener=Mixed(),decoder=decoded)
    assert not (tmp_path/'out'/'MANIFEST.json').exists()


def test_all_hours_absent_does_not_invent_native_universe(tmp_path):
    archive=Archive(missing=range(24));root=tmp_path/'out'
    with pytest.raises(ValueError,match='No native point universe'):
        run_day(STUDY,exact_plan(STUDY),root,opener=archive,decoder=decoded)
    assert len(archive.calls)==24 and not (root/'MANIFEST.json').exists()
    assert not list(root.glob('native-*.parquet'))


def test_independent_qa_rejects_finite_wrong_unit_conversion(tmp_path):
    def wrong(files,**kwargs):
        frame,evidence=decoded(files,**kwargs);frame['VISIBILITY_KM']=50.
        return frame,evidence
    with pytest.raises(ValueError,match='unit/wind conversion disagrees'):
        run_day(STUDY,exact_plan(STUDY),tmp_path/'out',opener=Archive(),decoder=wrong)
    assert not (tmp_path/'out'/'MANIFEST.json').exists()
