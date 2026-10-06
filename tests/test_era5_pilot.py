from pathlib import Path
import json

import pytest

from meteorology.era5.pilot import pilot_plan,run_pilot
from meteorology.era5.resources import Budget,Limits,LimitExceeded
from meteorology.era5.execution import process_native_day

STUDY=Path(__file__).parent/'fixtures/study.coastal-policy.v1.json'


def test_plan_exact_day_grid_boundary_and_caps():
    plan=pilot_plan(STUDY)
    assert plan['distribution_points']==9 and plan['expected_slots']==169
    assert plan['jobs'][1]['day']==['02'] and plan['jobs'][1]['time']==['00:00']
    assert plan['jobs'][1]['variable']==['total_precipitation']
    assert plan['limits']['transfer_bytes']==20*1024**2
    assert plan['limits']['staging_bytes']==64*1024**2
    assert plan['production_h3_eligible'] is False
    assert pilot_plan(STUDY)==plan


def test_changed_plan_rejected_before_credential_or_network(tmp_path):
    plan=pilot_plan(STUDY);plan['jobs'][0]['day']=['03']
    with pytest.raises(ValueError,match='Plan differs'):
        run_pilot(STUDY,plan,tmp_path/'out',credential_env_file=tmp_path/'absent')
    assert not (tmp_path/'out').exists()


def test_terms_failure_preserves_incomplete_without_jobs(tmp_path):
    class Provider:
        def __init__(self,**kwargs):pass
        def verify_terms(self,required):raise ValueError('not accepted')
    env=tmp_path/'auth.fixture';env.write_text('COPERNICUS_API_KEY=SYNTHETIC_NOT_REAL\n')
    out=tmp_path/'out'
    with pytest.raises(ValueError):
        run_pilot(STUDY,pilot_plan(STUDY),out,credential_env_file=env,provider_factory=Provider)
    assert json.loads((out/'RUN_STATE.json').read_text())['status']=='INCOMPLETE_NOT_PUBLISHED'
    assert not list(out.glob('job-*'))
    assert 'SYNTHETIC_NOT_REAL' not in (out/'PLAN.json').read_text()


def test_aggregate_disk_budget_includes_sibling_inputs(tmp_path):
    (tmp_path/'source.grib').write_bytes(b'x'*100)
    child=tmp_path/'processed';child.mkdir()
    b=Budget(Limits(staging_bytes=110),rss=lambda:0,staging_root=tmp_path)
    with pytest.raises(LimitExceeded):b.check_disk(child,additional_bytes=11)
    with pytest.raises(ValueError,match='escapes'):b.check_disk(tmp_path.parent)


def test_pending_mask_allows_explicit_native_pilot_but_default_rejects(tmp_path):
    source=tmp_path/'one.grib';source.write_bytes(b'SYNTHETIC')
    with pytest.raises(ValueError,match='proposed'):
        process_native_day(STUDY,[(source,'2025-01-01T00:00:00Z')],tmp_path/'default',day='2024-01-01',budget=Budget(Limits(),rss=lambda:0))
    # Explicit native purpose reaches strict decoder; a pending mask alone does not block source QA.
    with pytest.raises(ValueError):
        process_native_day(STUDY,[(source,'2025-01-01T00:00:00Z')],tmp_path/'native',day='2024-01-01',budget=Budget(Limits(),rss=lambda:0),native_source_pilot=True)
    assert (tmp_path/'native'/'RUN_STATE.json').exists()
    assert not (tmp_path/'native'/'MANIFEST.json').exists()


def generated_pilot_grib(path,*,boundary_only=False):
    from datetime import datetime,timedelta,timezone
    ec=pytest.importorskip('eccodes')
    params={167:283.15,168:278.15,165:3.,166:4.,151:101325.,164:.5,228:.001}
    start=datetime(2024,1,1,tzinfo=timezone.utc)
    with path.open('wb') as output:
        for hour in ([24] if boundary_only else range(24)):
            valid=start+timedelta(hours=hour)
            for param,value in params.items():
                if boundary_only and param!=228:continue
                init=valid-timedelta(hours=1) if param==228 else valid
                m=ec.codes_grib_new_from_samples('regular_ll_sfc_grib1')
                try:
                    for key,v in {'class':'ea','type':'fc' if param==228 else 'an',
                            'paramId':param,'Ni':3,'Nj':3,
                            'latitudeOfFirstGridPointInDegrees':49.5,'latitudeOfLastGridPointInDegrees':49,
                            'longitudeOfFirstGridPointInDegrees':234.5,'longitudeOfLastGridPointInDegrees':235,
                            'iDirectionIncrementInDegrees':.25,'jDirectionIncrementInDegrees':.25,
                            'dataDate':int(init.strftime('%Y%m%d')),'dataTime':int(init.strftime('%H%M')),
                            'stepType':'accum' if param==228 else 'instant',
                            'startStep':0,'endStep':1 if param==228 else 0}.items():ec.codes_set(m,key,v)
                    ec.codes_set_values(m,[value]*9);ec.codes_write(m,output)
                finally:ec.codes_release(m)


def test_real_generated_grib_through_concrete_mocked_transport_and_independent_qa(tmp_path):
    from meteorology.era5.transport import CDSHTTPProvider,API
    from .test_era5_transport import Opener,Response,HOST,CATALOGUE
    source=tmp_path/'day.grib';boundary=tmp_path/'boundary.grib'
    generated_pilot_grib(source);generated_pilot_grib(boundary,boundary_only=True)
    replies=[Response(CATALOGUE),Response({'licences':[{'id':'cc-by','revision':1}]})]
    for number,path in enumerate((source,boundary)):
        job=f'job-{number}'
        replies.extend([Response({'id':'reanalysis-era5-single-levels'}),
            Response({'links':[{'rel':'monitor','href':API+'/retrieve/v1/jobs/'+job}]},201),
            Response({'jobID':job,'status':'successful'}),
            Response({'asset':{'value':{'href':f'https://{HOST}/{job}','file:size':path.stat().st_size}}}),
            Response(path.read_bytes())])
    opener=Opener(*replies)
    def factory(**kwargs):return CDSHTTPProvider(**kwargs,opener=opener)
    env=tmp_path/'auth.fixture';env.write_text('COPERNICUS_API_KEY=SYNTHETIC_NOT_REAL\n')
    manifest=run_pilot(STUDY,pilot_plan(STUDY),tmp_path/'out',credential_env_file=env,provider_factory=factory)
    root=manifest.parent.parent
    qa=json.loads((root/'QA_RECEIPT.json').read_text())
    assert qa['checked_metric_rows']==81 and qa['complete_metric_rows']==81
    assert qa['final_h3_eligible'] is False and qa['period_complete'] is False
    assert json.loads(manifest.read_text())['native_source_pilot'] is True
    assert len(opener.calls)==12
    assert json.loads((root/'TRANSFER_BUDGET.json').read_text())['requests']==12
    # Independent QA catches altered daily values, rather than reusing aggregation.
    import pyarrow.parquet as pq
    from meteorology.era5.pilot_qa import validate_native_pilot
    frame=pq.read_table(manifest.parent/'native-daily.parquet').to_pandas()
    frame.loc[0,'ERA5_PRECIPITATION_MM']=999
    frame.to_parquet(manifest.parent/'native-daily.parquet',index=False)
    with pytest.raises(ValueError,match='numerical'):
        validate_native_pilot(manifest.parent,budget=Budget(Limits(),rss=lambda:0))
