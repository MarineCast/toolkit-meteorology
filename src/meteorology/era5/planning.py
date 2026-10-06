"""Network-free, bounded CDS request planning and credential-presence inspection."""
from __future__ import annotations

from datetime import date, timedelta
from importlib.util import find_spec
import math
import os
from pathlib import Path

from ..study import load_study_config

DATASET = 'reanalysis-era5-single-levels'
VARIABLES = ('2m_temperature', '2m_dewpoint_temperature', '10m_u_component_of_wind',
             '10m_v_component_of_wind', 'mean_sea_level_pressure', 'total_cloud_cover',
             'total_precipitation')
GIB = 1024**3


def access_preflight() -> dict:
    """Report presence only: no credential reads, client creation or provider call."""
    rc = Path(os.environ.get('CDSAPI_RC', str(Path.home()/'.cdsapirc'))).expanduser()
    return dict(cdsapi_installed=find_spec('cdsapi') is not None,
                credential_file_present=rc.is_file(),
                token_environment_present=bool(os.environ.get('CDSAPI_KEY')),
                endpoint_environment_present=bool(os.environ.get('CDSAPI_URL')),
                authenticated_access='not_checked', dataset_terms='not_checked_manual_acceptance_required',
                setup_url='https://cds.climate.copernicus.eu/how-to-api',
                terms_url=f'https://cds.climate.copernicus.eu/datasets/{DATASET}?tab=download')


def request_plan(study_config, *, start: str, end_exclusive: str,
                 max_days: int = 2, transfer_cap: int = 128*1024**2,
                 staging_cap: int = 1024*1024**2, memory_cap: int = 512*1024**2) -> dict:
    """Plan a bounded pilot with complete precipitation boundary; never submit it.

    The regular 0.25 degree grid is the CDS distribution grid, not the 31-km
    model-native representation and not a new fine-resolution observational grid.
    """
    identity = load_study_config(study_config, planning=True)
    if identity is None:
        raise ValueError('Explicit shared study selection is required for planning.')
    first, last = date.fromisoformat(start), date.fromisoformat(end_exclusive)
    requested = identity['requested_time']
    days = (last-first).days
    if not (date.fromisoformat(requested['start']) <= first < last <= date.fromisoformat(requested['end_exclusive'])):
        raise ValueError('Pilot interval must be inside the requested study window.')
    if type(max_days) is not int or not 1 <= max_days <= 31 or not 1 <= days <= max_days:
        raise ValueError('Plan requires 1 to max_days UTC days, max_days <= 31; chunk full history explicitly.')
    if any(type(v) is not int or v <= 0 for v in (transfer_cap, staging_cap, memory_cap)):
        raise ValueError('Resource caps must be positive integer bytes.')
    if staging_cap > 2*GIB or memory_cap > GIB:
        raise ValueError('Meteorology pilot caps exceed 2 GiB staging / 1 GiB memory allocation.')
    west,south,east,north = identity['study_config']['domain']['bbox_wgs84']
    # One full CDS grid interval halo, aligned outwards; not a coastline buffer.
    area = [math.ceil(north*4)/4+.25, math.floor(west*4)/4-.25,
            math.floor(south*4)/4-.25, math.ceil(east*4)/4+.25]
    if not (-90 <= area[2] < area[0] <= 90 and -180 <= area[1] < area[3] <= 180):
        raise ValueError('Aligned pilot halo crosses geographic limits.')
    cells = (round((area[0]-area[2])*4)+1)*(round((area[3]-area[1])*4)+1)
    hours = days*24+1
    jobs=[]
    current=first
    # Exact day selections avoid Cartesian year/month/day expansion and invalid dates.
    while current < last:
        month_days=[]
        year,month=current.year,current.month
        while current < last and (current.year,current.month)==(year,month):
            month_days.append(f'{current.day:02}')
            current += timedelta(days=1)
        jobs.append(dict(product_type=['reanalysis'], variable=list(VARIABLES),
                         year=[str(year)], month=[f'{month:02}'], day=month_days,
                         time=[f'{h:02}:00' for h in range(24)], area=area,
                         grid=[.25,.25], data_format='grib', download_format='unarchived'))
    # Mandatory next-day 00UTC for the last day's 23:00–24:00 precipitation.
    jobs.append(dict(product_type=['reanalysis'], variable=['total_precipitation'],
                     year=[str(last.year)], month=[f'{last.month:02}'], day=[f'{last.day:02}'],
                     time=['00:00'], area=area, grid=[.25,.25], data_format='grib',
                     download_format='unarchived'))
    from .resources import memory_estimate
    memory=memory_estimate(cells,source_bytes=transfer_cap)
    decoded = cells*(days*24*len(VARIABLES)+1)*8
    if (decoded > staging_cap or memory['normalized_full_day_estimate_bytes']+transfer_cap > staging_cap
            or memory['bounded_working_set_estimate_bytes'] > memory_cap):
        raise ValueError('Decoded lower-bound or conservative one-day working set exceeds cap.')
    return dict(dataset=DATASET, source_family='ERA5_separate_retrospective_baseline',
                method='era5-cds-hourly-plan-v1', study_identity=identity,
                interval=dict(start=start,end_exclusive=end_exclusive,timezone='UTC'),
                execution='planning_only_no_network', jobs=jobs, unique_valid_hours=hours,
                distribution_grid_points=cells, decoded_float64_payload_lower_bound_bytes=decoded,
                memory_preflight=memory,
                transfer_estimate='unknown_until_measured_pilot; numeric cap is not an estimate',
                caps=dict(transfer_bytes=transfer_cap,staging_bytes=staging_cap,memory_bytes=memory_cap,
                          compute_seconds=3600,workers=1),
                spatial_support='aligned_acquisition_envelope_plus_0.25_degree_native_sampling_halo_not_reporting_mask',
                requested_reporting='coastal_policy_requires_materialized_mask_and_registry',
                retention='one source chunk plus compact normalized native samples and receipts; no repeated hourly raster archives',
                production_eligible=False, access=access_preflight())
