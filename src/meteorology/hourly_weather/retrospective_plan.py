"""Network-free HRRR historical era and bounded UTC pilot planning."""
from datetime import date, timedelta
from ..study import load_study_config

# Calendar implementation dates; no assertion that cutover occurred at 00UTC.
TRANSITIONS=(('2014-09-30','HRRRv1'),('2016-08-23','HRRRv2'),
             ('2018-07-12','HRRRv3'),('2020-12-02','HRRRv4'))
HISTORY_SOURCE='https://rapidrefresh.noaa.gov/hrrr/'


def era_for_day(day: str) -> dict:
    selected=date.fromisoformat(day)
    version=None
    for boundary,candidate in TRANSITIONS:
        edge=date.fromisoformat(boundary)
        if selected==edge:
            return dict(day=day,status='TRANSITION_DAY_VALIDATE_CYCLE_METADATA',expected_era=candidate,
                        qualification='implementation_calendar_not_archive_completeness')
        if selected>edge: version=candidate
    return dict(day=day,status='OPERATIONAL_ERA_CANDIDATE' if version else 'PRE_OPERATIONAL_UNAVAILABLE',
                expected_era=version,qualification='actual_archive_cycle_and_field_availability_unverified')


def request_plan(study_config, *, start: str, end_exclusive: str, max_days: int=2) -> dict:
    identity=load_study_config(study_config,planning=True)
    if identity is None: raise ValueError('HRRR planning requires explicit shared study selection.')
    first,last=date.fromisoformat(start),date.fromisoformat(end_exclusive)
    days=(last-first).days
    if not 1<=days<=max_days<=7:
        raise ValueError('HRRR pilot requires 1–max_days UTC days, max_days <= 7.')
    requested=identity['requested_time']
    if first<date.fromisoformat(requested['start']) or last>date.fromisoformat(requested['end_exclusive']):
        raise ValueError('HRRR pilot is outside study request.')
    eras=[era_for_day((first+timedelta(days=i)).isoformat()) for i in range(days)]
    return dict(source_family='HRRR_separate_operational_analysis',timezone='UTC',
                study_identity=identity,start=start,end_exclusive=end_exclusive,days=eras,
                required_core_cycles=days*24,forecast_hour=0,core_product='sfc',
                precipitation='not_in_f00_core; separate verified one-hour forecast accumulation required; f00 PRATE is not rain',
                source_history=HISTORY_SOURCE,archive='https://registry.opendata.aws/noaa-hrrr-pds/',
                access='public_no_account_required',coverage='unverified_by_hour_field_and_native_footprint',
                expected_grid='CONUS_3km; qualify decoded footprint per historical era; do not extend outside coverage',
                caps=dict(workers=1,transfer_bytes=768*1024**2,requests=days*24*9,
                          staging_bytes=2*1024**3,memory_bytes=1024**3,compute_seconds=3600),
                prior_measurement='old bounded domain only: ~266.5 MiB received/day; revised full coastal domain not yet measured',
                retention='stream each selected GRIB through verified decode and compact native/R5 samples; retain receipt/crosswalk, not every decoded hourly raster',
                production_eligible=False,execution='planning_only_no_network',
                source_transition_policy='do not label an entire calendar day one version at cutovers without source metadata',
                historical_gap='2009-01-01 through pre-operational 2014-09-30 remains unavailable in HRRR; ERA5 stays separate')


def main(argv=None):
    import argparse,json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start',required=True)
    parser.add_argument('--end-exclusive',required=True)
    args=parser.parse_args(argv)
    print(json.dumps(request_plan(None,start=args.start,end_exclusive=args.end_exclusive),indent=2))
    return 0
