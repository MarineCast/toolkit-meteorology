from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd
import pytest

from meteorology.cli import main
from meteorology.era5.planning import request_plan, access_preflight, DATASET
from meteorology.era5.source import normalize_message, consolidate, PARAMETERS
from meteorology.era5.daily import utc_daily

STUDY=Path(__file__).parent/'fixtures/study.coastal-policy.v1.json'


def message(param,valid,values=(1,2),expver=1):
    name,units=PARAMETERS[param]
    tp=name=='tp'
    return dict(dataset=DATASET,product_type='reanalysis',centre='ecmf',mars_class='ea',mars_stream='oper',
        data_type='fc' if tp else 'an',paramId=param,units=units,gridType='regular_ll',
        iDirectionIncrementInDegrees=.25,jDirectionIncrementInDegrees=.25,
        uvRelativeToGrid=0,valid_time_utc=valid.isoformat(),
        init_time_utc=(valid-timedelta(hours=1) if tp else valid).isoformat(),
        retrieved_at_utc='2025-01-01T00:00:00+00:00',source_sha256='a'*64,expver=expver,
        stepType='accum' if tp else 'instant',startStep=0,endStep=1 if tp else 0,step_units='hours')


def fixture_day():
    start=datetime(2024,1,1,tzinfo=timezone.utc)
    frames=[]
    for hour in range(25):
        valid=start+timedelta(hours=hour)
        for param,value in [(167,283.15),(168,278.15),(165,3),(166,4),(151,101325),(164,.5),(228,.001)]:
            if hour==24 and param!=228: continue
            frames.append(normalize_message(message(param,valid),[49,49],[235,235.25],[value,value]))
    return pd.concat(frames,ignore_index=True)


def test_plan_precise_year_and_month_boundary_no_request(tmp_path,monkeypatch):
    monkeypatch.setenv('CDSAPI_KEY','presence-only-test-not-a-secret')
    plan=request_plan(STUDY,start='2024-12-31',end_exclusive='2025-01-02')
    assert plan['unique_valid_hours']==49
    assert len(plan['jobs'])==3
    assert [j['year'] for j in plan['jobs']]==[['2024'],['2025'],['2025']]
    assert plan['jobs'][-1]['time']==['00:00']
    assert plan['jobs'][-1]['variable']==['total_precipitation']
    assert plan['production_eligible'] is False
    assert plan['access']['token_environment_present'] is True
    assert 'presence-only-test' not in json.dumps(plan)
    assert plan['jobs'][0]['data_format']=='grib'
    assert plan['caps']['workers']==1
    with pytest.raises(ValueError,match='max_days'):
        request_plan(STUDY,start='2009-01-01',end_exclusive='2027-01-01')
    with pytest.raises(ValueError,match='allocation'):
        request_plan(STUDY,start='2024-01-01',end_exclusive='2024-01-03',staging_cap=3*1024**3)
    with pytest.raises(ValueError,match='lower-bound'):
        request_plan(STUDY,start='2024-01-01',end_exclusive='2024-01-03',staging_cap=1)
    assert list(tmp_path.iterdir())==[]


def test_cli_plan_accepts_pending_geometry_without_writes(capsys):
    assert main(['--study-config',str(STUDY),'era5-plan','--start','2024-01-01','--end-exclusive','2024-01-03'])==0
    report=json.loads(capsys.readouterr().out)
    assert report['execution']=='planning_only_no_network'
    assert report['study_identity']['domain_status']=='proposed'


@pytest.mark.parametrize('mutation,match',[
    (lambda m:m.update(units='mm'), 'units'),
    (lambda m:m.update(gridType='reduced_gg'), 'grid'),
    (lambda m:m.update(stepType='instant'), 'one-hour'),
    (lambda m:m.update(endStep=0), 'one-hour'),
    (lambda m:m.update(startStep=0,endStep=12), 'one-hour'),
    (lambda m:m.update(expver=2), 'expver'),
    (lambda m:m.update(mars_class='od'), 'centre/class'),
    (lambda m:m.update(valid_time_utc='2024-01-01T01:00:00'), 'UTC'),
    (lambda m:m.update(source_sha256='x'*64), 'SHA256'),
])
def test_strict_metadata_prevents_source_substitution(mutation,match):
    m=message(228,datetime(2024,1,1,1,tzinfo=timezone.utc))
    mutation(m)
    with pytest.raises(ValueError,match=match):
        normalize_message(m,[49],[235],[.001])


def test_wind_requires_earth_basis_and_bitmap_missing_is_not_zero():
    m=message(165,datetime(2024,1,1,tzinfo=timezone.utc))
    m['uvRelativeToGrid']=1
    with pytest.raises(ValueError,match='earth-relative'): normalize_message(m,[49],[235],[3])
    m['uvRelativeToGrid']=0
    frame=normalize_message(m,[49],[235],[np.nan])
    assert frame.VALUE.isna().all()
    assert frame.SOURCE_LON.iloc[0]==-125


def test_final_priority_is_explicit_and_duplicate_versions_fail():
    first=fixture_day()
    preliminary=first.copy()
    preliminary.EXPVER=5
    preliminary.CONSOLIDATION='preliminary_ERA5T'
    preliminary.VALUE*=2
    merged=consolidate(pd.concat([preliminary,first],ignore_index=True))
    assert set(merged.EXPVER)=={1}
    assert len(merged)==len(first)
    with pytest.raises(ValueError,match='Duplicate'):
        consolidate(pd.concat([first,first],ignore_index=True))
    with pytest.raises(ValueError,match='after requested as-of'):
        consolidate(first,as_of_utc='2024-01-03T00:00:00Z')
    missing_final=first.copy()
    missing_final.loc[missing_final.PARAMETER=='tp','VALUE']=np.nan
    merged=consolidate(pd.concat([preliminary,missing_final],ignore_index=True))
    assert merged[merged.PARAMETER=='tp'].VALUE.isna().all()


def test_daily_independent_units_rh_wind_and_precip_boundary():
    records=fixture_day()
    # Daily sum excludes Jan 1 00 and includes Jan 2 00.
    records.loc[(records.PARAMETER=='tp') & (records.VALID_TIME_UTC=='2024-01-01T00:00:00+00:00'),'VALUE']=1
    records.loc[(records.PARAMETER=='tp') & (records.VALID_TIME_UTC=='2024-01-02T00:00:00+00:00'),'VALUE']=.002
    result=utc_daily(records,day='2024-01-01')
    assert len(result)==2
    row=result.iloc[0]
    assert row.ERA5_TEMPERATURE_2M_C==pytest.approx(10)
    assert row.ERA5_WIND_SPEED_10M_MS==pytest.approx(5)
    assert row.ERA5_MEAN_SEA_LEVEL_PRESSURE_HPA==pytest.approx(1013.25)
    assert row.ERA5_TOTAL_CLOUD_COVER_PCT==pytest.approx(50)
    assert row.ERA5_PRECIPITATION_MM==pytest.approx(25)
    # Independent evaluation of e_s(Td)/e_s(T).
    from math import exp
    expected=100*(6.112*exp(17.67*5/(5+243.5)))/(6.112*exp(17.67*10/(10+243.5)))
    assert row.ERA5_RELATIVE_HUMIDITY_2M_PCT==pytest.approx(expected)
    assert row.ERA5_VISIBILITY_KM is None
    assert row.ERA5_VISIBILITY_KM_STATUS=='UNAVAILABLE'
    assert row.AVAILABLE_AT_UTC is None


def test_missing_midnight_precip_and_missing_hour_do_not_extrapolate():
    records=fixture_day()
    records=records[~((records.PARAMETER=='tp') & (records.VALID_TIME_UTC=='2024-01-02T00:00:00+00:00'))]
    records.loc[(records.PARAMETER=='t2m') & (records.VALID_TIME_UTC=='2024-01-01T12:00:00+00:00'),'VALUE']=np.nan
    row=utc_daily(records,day='2024-01-01').iloc[0]
    assert row.ERA5_PRECIPITATION_MM is None
    assert row.ERA5_PRECIPITATION_MM_STATUS=='PARTIAL'
    assert row.ERA5_PRECIPITATION_MM_VALID_HOURS==23
    assert row.ERA5_TEMPERATURE_2M_C is None
    assert row.ERA5_TEMPERATURE_2M_C_COVERAGE_FRACTION==pytest.approx(23/24)
    assert row.ERA5_WIND_SPEED_10M_MS_STATUS=='COMPLETE'


def test_dry_is_valid_zero_but_absent_is_unavailable():
    records=fixture_day()
    records.loc[records.PARAMETER=='tp','VALUE']=0
    assert utc_daily(records,day='2024-01-01').iloc[0].ERA5_PRECIPITATION_MM==0
    records=records[records.PARAMETER!='tp']
    row=utc_daily(records,day='2024-01-01').iloc[0]
    assert row.ERA5_PRECIPITATION_MM is None
    assert row.ERA5_PRECIPITATION_MM_STATUS=='UNAVAILABLE'


def test_grid_changes_and_interval_corruption_fail():
    records=fixture_day()
    bad=records.copy()
    bad.loc[0,'SOURCE_GRID_HASH']='b'*64
    with pytest.raises(ValueError,match='mix grids'):utc_daily(bad,day='2024-01-01')
    bad=records.copy()
    bad.loc[bad.PARAMETER=='tp','INTERVAL_START_UTC']='2023-12-31T00:00:00Z'
    with pytest.raises(ValueError,match='interval start'):utc_daily(bad,day='2024-01-01')


def test_hrrr_eras_and_no_silent_substitution():
    from meteorology.hourly_weather.retrospective_plan import era_for_day, request_plan as hrrr_plan
    assert era_for_day('2009-01-01')['status']=='PRE_OPERATIONAL_UNAVAILABLE'
    assert era_for_day('2014-09-30')['status']=='TRANSITION_DAY_VALIDATE_CYCLE_METADATA'
    assert era_for_day('2016-08-24')['expected_era']=='HRRRv2'
    assert era_for_day('2018-07-13')['expected_era']=='HRRRv3'
    assert era_for_day('2024-01-01')['expected_era']=='HRRRv4'
    plan=hrrr_plan(STUDY,start='2024-01-01',end_exclusive='2024-01-03')
    assert plan['required_core_cycles']==48
    assert plan['production_eligible'] is False
    assert plan['timezone']=='UTC'
    assert 'not_in_f00_core' in plan['precipitation']


def test_entire_missing_grid_point_fails_universe_gate():
    records=fixture_day()
    with pytest.raises(ValueError,match='universe is incomplete'):
        utc_daily(records[records.SOURCE_GRID_INDEX==0],day='2024-01-01')


def test_generated_grib_decode_metadata_and_bitmap(tmp_path):
    ec=pytest.importorskip('eccodes')
    from meteorology.era5.source import decode_grib
    m=ec.codes_grib_new_from_samples('regular_ll_sfc_grib1')
    path=tmp_path/'synthetic-era5-message.grib'
    try:
        for key,value in dict(**{'class':'ea'},Ni=2,Nj=1,latitudeOfFirstGridPointInDegrees=49,
                latitudeOfLastGridPointInDegrees=49,longitudeOfFirstGridPointInDegrees=235,
                longitudeOfLastGridPointInDegrees=235.25,iDirectionIncrementInDegrees=.25,
                jDirectionIncrementInDegrees=.25,dataDate=20240101,dataTime=0,bitmapPresent=1).items():
            ec.codes_set(m,key,value)
        ec.codes_set(m,'missingValue',9999)
        ec.codes_set_values(m,[283.15,9999])
        with path.open('wb') as f:ec.codes_write(m,f)
    finally:ec.codes_release(m)
    frames=list(decode_grib(path,retrieved_at_utc='2025-01-01T00:00:00Z'))
    assert len(frames)==1
    assert frames[0].VALUE.iloc[0]==pytest.approx(283.15)
    assert pd.isna(frames[0].VALUE.iloc[1])
    assert set(frames[0].EXPVER)=={1}
    assert frames[0].PARAMETER.iloc[0]=='t2m'
