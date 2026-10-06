"""Exact one-day native ERA5 pilot; plan is network-free, run uses bounded HTTPS."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path

from ..study import load_study_config
from .execution import process_native_day
from .jobs import request_identity,run_job
from .planning import DATASET,VARIABLES
from .resources import Budget,Limits,atomic_json,memory_estimate
from .source import decode_grib
from .transport import CDSHTTPProvider

MIB=1024**2


def pilot_plan(study_config):
    identity=load_study_config(study_config,planning=True)
    if identity is None:raise ValueError('Explicit study selection required.')
    area=[49.5,-125.5,49.0,-125.0]
    west,south,east,north=identity['study_config']['domain']['bbox_wgs84']
    if not (west<=area[1]<area[3]<=east and south<=area[2]<area[0]<=north):
        raise ValueError('Pilot area lies outside acquisition envelope.')
    window=identity['requested_time']
    if not window['start']<='2024-01-01'<'2024-01-02'<=window['end_exclusive']:
        raise ValueError('Pilot day lies outside requested study window.')
    # Exact public study canonical identity from the validated loader.
    sha=identity['config_sha256']
    common=dict(product_type=['reanalysis'],year=['2024'],month=['01'],area=area,
                grid=[.25,.25],data_format='grib',download_format='unarchived')
    jobs=[dict(common,variable=list(VARIABLES),day=['01'],time=[f'{h:02}:00' for h in range(24)]),
          dict(common,variable=['total_precipitation'],day=['02'],time=['00:00'])]
    plan=dict(version='era5-native-pilot-v1',dataset=DATASET,day='2024-01-01',
        study_config_sha256=sha,purpose='native_source_qualification_not_final_H3',
        area_nwse=area,distribution_points=9,jobs=jobs,
        request_identities=[request_identity(job,study_sha256=sha) for job in jobs],
        limits=vars(Limits(transfer_bytes=20*MIB,staging_bytes=64*MIB,memory_bytes=512*MIB,requests=64,seconds=2700)),
        download_reservation_per_job=8*MIB,poll_seconds=30,poll_window_seconds=600,max_polls=20,
        storage_hosts=['object-store.os-api.cci2.ecmwf.int'],required_licences=[dict(id='cc-by',revision=1)],
        memory_estimate=memory_estimate(9,source_bytes=16*MIB),decoded_values_lower_bound=12168,
        expected_slots=169,expected_expvers=[1,5],production_h3_eligible=False)
    plan['plan_sha256']=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return plan


def _credential(path):
    # Explicit local alias only. Never write a token to outputs or copy env files.
    matches=[]
    for line in Path(path).read_text().splitlines():
        if line.strip().startswith('COPERNICUS_API_KEY='):
            matches.append(line.split('=',1)[1].strip().strip('\"\''))
    if len(matches)!=1:raise ValueError('Exactly one configured token alias required.')
    return matches[0]


def run_pilot(study_config,plan,output,*,credential_env_file,provider_factory=CDSHTTPProvider):
    if plan!=pilot_plan(study_config):
        raise ValueError('Plan differs from exact reviewed study/source/date/region/caps.')
    root=Path(output);root.mkdir(parents=True,exist_ok=True)
    with (root/'.pilot.lock').open('a+b') as lease:
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another pilot worker owns this output.') from None
        plan_path=root/'PLAN.json'
        if plan_path.exists() and json.loads(plan_path.read_text())!=plan:
            raise ValueError('Owned output already belongs to another plan.')
        budget=Budget(Limits(**plan['limits']),staging_root=root)
        journal=root/'TRANSFER_BUDGET.json';budget.bind_journal(journal)
        budget.check_disk(root,additional_bytes=65536)
        atomic_json(plan_path,plan)
        provider=provider_factory(key=_credential(credential_env_file),budget=budget,
            storage_hosts=tuple(plan['storage_hosts']),download_cap=plan['download_reservation_per_job'])
        try:
            # No auto-acceptance. Fresh catalogue + accepted-list verification on resume too.
            terms=provider.verify_terms(plan['required_licences'])
            atomic_json(root/'TERMS_RECEIPT.json',terms)
            sources=[]
            for n,(request,identity) in enumerate(zip(plan['jobs'],plan['request_identities'])):
                job_root=root/f'job-{n}'
                result=run_job(provider,request,job_root,study_sha256=plan['study_config_sha256'],
                    budget=budget,approved_identity=identity,
                    download_reservation=plan['download_reservation_per_job'],
                    poll_seconds=plan['poll_seconds'],poll_window_seconds=plan['poll_window_seconds'],
                    max_polls=plan['max_polls'],journal_path=journal)
                sources.append((job_root/'source.grib',result['retrieved_at_utc']))
            expected={(lat,lon) for lat in [49,49.25,49.5] for lon in [-125.5,-125.25,-125]}
            # Validate exact requested small grid before source processing can allocate larger batches.
            for source,retrieved in sources:
                for frame in decode_grib(source,retrieved_at_utc=retrieved,budget=budget):
                    if set(zip(frame.SOURCE_LAT,frame.SOURCE_LON))!=expected:
                        raise ValueError('Delivered GRIB grid differs from exact pilot grid.')
            manifest=process_native_day(study_config,sources,root/'processed',day=plan['day'],
                                        budget=budget,native_source_pilot=True)
            from .pilot_qa import validate_native_pilot
            qa=validate_native_pilot(root/'processed',budget=budget)
            budget.check_disk(root,additional_bytes=65536)
            atomic_json(root/'QA_RECEIPT.json',qa)
            atomic_json(root/'RUN_STATE.json',dict(status='NATIVE_PROCESSING_AND_QA_COMPLETE_NOT_H3_RELEASE',
                        manifest=str(manifest.relative_to(root)),final_h3_eligible=False))
            return manifest
        except BaseException:
            atomic_json(root/'RUN_STATE.json',dict(status='INCOMPLETE_NOT_PUBLISHED',budget=budget.receipt()))
            raise
        finally:budget.persist()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan','run'])
    parser.add_argument('--study-config',required=True)
    parser.add_argument('--plan-json')
    parser.add_argument('--output')
    parser.add_argument('--credential-env-file')
    args=parser.parse_args()
    try:
        if args.action=='plan':print(json.dumps(pilot_plan(args.study_config),indent=2));return
        if not all((args.plan_json,args.output,args.credential_env_file)):
            parser.error('run requires --plan-json --output --credential-env-file')
        result=run_pilot(args.study_config,json.loads(Path(args.plan_json).read_text()),args.output,
                         credential_env_file=args.credential_env_file)
        print(json.dumps({'status':'NATIVE_CHECKPOINT','manifest':str(result),'final_h3_eligible':False}))
    except Exception as error:
        print(json.dumps({'status':'FAILED_NOT_PUBLISHED','error_class':type(error).__name__,
                          'detail':'Provider details and credentials redacted; inspect owned state receipts.'}))
        raise SystemExit(1) from None


if __name__=='__main__':main()
