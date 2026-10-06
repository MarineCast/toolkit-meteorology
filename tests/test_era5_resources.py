from io import BytesIO
from pathlib import Path
import json

import pytest

from meteorology.era5.resources import Budget,Limits,LimitExceeded,memory_estimate,bounded_transfer
from meteorology.era5.planning import request_plan
from meteorology.era5.execution import process_native_day

STUDY=Path(__file__).parent/'fixtures/study.coastal-policy.v1.json'


def test_review_memory_reproduction_is_rejected():
    plan=request_plan(STUDY,start='2024-01-01',end_exclusive='2024-01-02')
    assert plan['distribution_grid_points']==936
    with pytest.raises(ValueError,match='working set exceeds cap'):
        request_plan(STUDY,start='2024-01-01',end_exclusive='2024-01-02',memory_cap=5241600)
    model=plan['memory_preflight']
    assert model['normalized_full_day_estimate_bytes']>64349430
    assert model['bounded_working_set_estimate_bytes']>5241600
    assert model['point_batch']==64
    assert model['bounded_working_set_estimate_bytes']<512*1024**2
    assert 'not_OS_hard' in model['policy']


def fake_budget(**limits):
    return Budget(Limits(**limits),rss=lambda:0)


class Response(BytesIO):
    def __init__(self,data,length=True):
        super().__init__(data);self.headers={'Content-Length':str(len(data))} if length else {}


def test_transfer_reserves_before_open_and_never_exceeds_payload_allowance(tmp_path):
    calls=[]
    budget=fake_budget(requests=1,transfer_bytes=4)
    def opener():calls.append(1);return Response(b'abcd')
    receipt=bounded_transfer(opener,tmp_path/'one.grib',budget=budget,reservation_bytes=4)
    assert (tmp_path/'one.grib').read_bytes()==b'abcd'
    assert json.loads(receipt.read_text())['status']=='COMPLETE'
    with pytest.raises(LimitExceeded,match='before network open'):
        bounded_transfer(opener,tmp_path/'two.grib',budget=budget,reservation_bytes=4)
    assert len(calls)==1
    assert budget.received==4


def test_unknown_length_overflow_and_interruption_leave_no_terminal_payload(tmp_path):
    budget=fake_budget(transfer_bytes=4)
    with pytest.raises(LimitExceeded,match='before reading extra'):
        bounded_transfer(lambda:Response(b'abcdef',length=False),tmp_path/'overflow.grib',budget=budget,reservation_bytes=4)
    assert not (tmp_path/'overflow.grib').exists()
    assert (tmp_path/'overflow.grib.partial').stat().st_size==4
    assert budget.received==4
    assert json.loads((tmp_path/'overflow.grib.receipt.json').read_text())['status']=='INCOMPLETE_NOT_PROMOTED'
    class Interrupted(Response):
        def read(self,n):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        bounded_transfer(lambda:Interrupted(b'abcd'),tmp_path/'interrupted-run/interrupt.grib',budget=fake_budget(),reservation_bytes=4)
    assert not (tmp_path/'interrupted-run/interrupt.grib').exists()
    assert json.loads((tmp_path/'interrupted-run/interrupt.grib.receipt.json').read_text())['status']=='INCOMPLETE_NOT_PROMOTED'


def test_rss_time_disk_worker_limits_stop():
    with pytest.raises(ValueError,match='one worker'):Budget(Limits(workers=2))
    b=Budget(Limits(memory_bytes=100),rss=lambda:90)
    with pytest.raises(LimitExceeded,match='planned allocation'):b.checkpoint(additional_memory=11)
    clock=[0]
    b=Budget(Limits(seconds=1),rss=lambda:0,clock=lambda:clock[0])
    clock[0]=2
    with pytest.raises(LimitExceeded,match='Compute time'):b.checkpoint()


def test_pending_geometry_blocks_executor_before_output(tmp_path):
    with pytest.raises(ValueError,match='proposed'):
        process_native_day(STUDY,[(tmp_path/'missing.grib','2025-01-01T00:00:00Z')],tmp_path/'out',day='2024-01-01',budget=fake_budget())
    assert not (tmp_path/'out').exists()


def test_interrupting_normalization_preserves_checkpoint_without_manifest(tmp_path,monkeypatch):
    import meteorology.era5.execution as execution
    from meteorology.study import load_study_config
    # Only synthetic test metadata opens the otherwise production-blocked path.
    identity=load_study_config(STUDY,planning=True)
    monkeypatch.setattr(execution,'load_study_config',lambda _:identity)
    source=tmp_path/'fixture.grib';source.write_bytes(b'SYNTHETIC')
    def interrupted(*args,**kwargs):
        raise KeyboardInterrupt()
        yield
    monkeypatch.setattr(execution,'decode_grib',interrupted)
    with pytest.raises(KeyboardInterrupt):
        process_native_day(STUDY,[(source,'2025-01-01T00:00:00Z')],tmp_path/'out',day='2024-01-01',budget=fake_budget())
    assert source.read_bytes()==b'SYNTHETIC'
    assert not (tmp_path/'out/MANIFEST.json').exists()
    assert json.loads((tmp_path/'out/RUN_STATE.json').read_text())['status']=='INCOMPLETE_NOT_PUBLISHED'
    with pytest.raises(FileExistsError):
        process_native_day(STUDY,[(source,'2025-01-01T00:00:00Z')],tmp_path/'out',day='2024-01-01',budget=fake_budget())


def test_transfer_reservation_survives_new_worker_after_interruption(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        bounded_transfer(lambda:(_ for _ in ()).throw(KeyboardInterrupt()),tmp_path/'one.grib',
                         budget=fake_budget(requests=1,transfer_bytes=4),reservation_bytes=4)
    calls=[]
    with pytest.raises(LimitExceeded,match='before network open'):
        bounded_transfer(lambda:calls.append(1),tmp_path/'two.grib',
                         budget=fake_budget(requests=1,transfer_bytes=4),reservation_bytes=4)
    assert calls==[]
    assert json.loads((tmp_path/'TRANSFER_BUDGET.json').read_text())['requests']==1


def test_failed_budget_rebinding_cannot_bypass_persistent_limits(tmp_path):
    bounded_transfer(lambda:Response(b'abcd'),tmp_path/'one.grib',budget=fake_budget(transfer_bytes=4),reservation_bytes=4)
    changed=fake_budget(transfer_bytes=8)
    for _ in range(2):
        with pytest.raises(ValueError,match='original resource caps'):
            bounded_transfer(lambda:Response(b'abcd'),tmp_path/'two.grib',budget=changed,reservation_bytes=4)
    assert not (tmp_path/'two.grib').exists()


def test_bounded_native_pipeline_streams_two_point_batches_with_nullable_schema(tmp_path):
    ec=pytest.importorskip('eccodes')
    import h3
    import hashlib
    import pyarrow.parquet as pq
    config=json.loads(STUDY.read_bytes())
    config['domain'].update(status='approved',geometry_status='source_relative_validated',approval=dict(
        approved_at='2026-10-06T00:00:00Z',source_message_id='synthetic',scope='rectangular_selection_only',statement='SYNTHETIC'))
    config['domain']['selection_policy']['mask_status']='source_relative_validated'
    cell=h3.latlng_to_cell(49,-125,5)
    ids=(cell+'\n').encode()
    config['grid_registry'].update(status='validated',mask_revision='synthetic',mask_sha256='a'*64,
        memberships=[dict(resolution=5,role='water_reporting',relative_path='../Data/test.txt',count=1,sha256=hashlib.sha256(ids).hexdigest())])
    study=tmp_path/'study.json';study.write_text(json.dumps(config))
    source=tmp_path/'synthetic.grib'
    m=ec.codes_grib_new_from_samples('regular_ll_sfc_grib1')
    try:
        for key,value in dict(**{'class':'ea'},Ni=10,Nj=7,latitudeOfFirstGridPointInDegrees=50.5,
            latitudeOfLastGridPointInDegrees=49,longitudeOfFirstGridPointInDegrees=235,
            longitudeOfLastGridPointInDegrees=237.25,iDirectionIncrementInDegrees=.25,
            jDirectionIncrementInDegrees=.25,dataDate=20240101,dataTime=0,bitmapPresent=1).items():ec.codes_set(m,key,value)
        ec.codes_set(m,'missingValue',9999)
        ec.codes_set_values(m,[283.15]*64+[9999]*6)
        with source.open('wb') as f:ec.codes_write(m,f)
    finally:ec.codes_release(m)
    path=process_native_day(study,[(source,'2025-01-01T00:00:00Z')],tmp_path/'processed',day='2024-01-01',budget=fake_budget())
    manifest=json.loads(path.read_text())
    assert manifest['grid_points']==70
    daily=pq.read_table(path.parent/'native-daily.parquet').to_pandas()
    assert len(daily)==70
    assert daily.ERA5_TEMPERATURE_2M_C.isna().all() # one hour is incomplete, not mean-filled
    assert set(daily.ERA5_TEMPERATURE_2M_C_STATUS)<= {'PARTIAL','UNAVAILABLE'}
    assert pq.ParquetFile(path.parent/'native-daily.parquet').num_row_groups==2
    assert source.is_file()
    assert manifest['period_complete'] is False


def test_interleaved_reused_budget_never_rolls_back(tmp_path):
    from io import BytesIO
    from meteorology.era5.resources import Budget,Limits,bounded_transfer,LimitExceeded
    class Response(BytesIO):
        def __init__(self):
            super().__init__(b'abcd'); self.headers={'Content-Length':'4'}
    limits=Limits(requests=2,transfer_bytes=8)
    a=Budget(limits,rss=lambda:0);b=Budget(limits,rss=lambda:0)
    bounded_transfer(Response,tmp_path/'a.grib',budget=a,reservation_bytes=4)
    bounded_transfer(Response,tmp_path/'b.grib',budget=b,reservation_bytes=4)
    with pytest.raises(LimitExceeded):
        bounded_transfer(Response,tmp_path/'c.grib',budget=a,reservation_bytes=4)
    state=json.loads((tmp_path/'TRANSFER_BUDGET.json').read_text())
    assert state['requests']==2 and state['received_transfer_bytes']==8
    assert not (tmp_path/'c.grib').exists()


def test_rebinding_same_budget_preserves_elapsed_without_double_count(tmp_path):
    from meteorology.era5.resources import Budget,Limits
    clock=[0]
    b=Budget(Limits(),rss=lambda:0,clock=lambda:clock[0])
    path=tmp_path/'budget.json';b.bind_journal(path)
    clock[0]=5;b.persist();b.bind_journal(path)
    assert b.receipt()['elapsed_seconds']==5
    clock[0]=8;b.bind_journal(path)
    assert b.receipt()['elapsed_seconds']==8
