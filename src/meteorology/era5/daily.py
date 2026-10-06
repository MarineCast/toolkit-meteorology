"""UTC-day wide native-grid summaries; incomplete slots never become zero."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json

import numpy as np
import pandas as pd

from .source import consolidate

METHOD = 'era5-utc-native-complete-metric-daily-v1'
# Instantaneous hourly values represent samples, not observed within-hour extrema.
METRICS = {
 'TEMPERATURE_2M_C': ('t2m','mean','degC'),
 'DEWPOINT_2M_C': ('d2m','mean','degC'),
 'RELATIVE_HUMIDITY_2M_PCT': ('rh','mean','percent'),
 'U_WIND_10M_MS': ('u10','mean','m/s'),
 'V_WIND_10M_MS': ('v10','mean','m/s'),
 'WIND_SPEED_10M_MS': ('speed','mean','m/s'),
 'MEAN_SEA_LEVEL_PRESSURE_HPA': ('msl','mean','hPa'),
 'TOTAL_CLOUD_COVER_PCT': ('tcc','mean','percent'),
 'PRECIPITATION_MM': ('tp','sum','mm'),
 'VISIBILITY_KM': (None,'unavailable','km'),
 'WIND_GUST_SURFACE_MS': (None,'unavailable','m/s'),
}


def utc_daily(records: pd.DataFrame, *, day: str, as_of_utc: str | None = None, expected_native_indices=None, budget=None) -> pd.DataFrame:
    """One native grid, one UTC day, field-specific strict 24-slot completeness.

    No H3 downscaling or water-mask approximation occurs here. RH uses a versioned
    saturation-over-water approximation; it is not a direct archived parameter.
    """
    start=datetime.combine(date.fromisoformat(day),datetime.min.time(),timezone.utc)
    end=start+timedelta(days=1)
    if budget is not None:
        from .resources import ROW_BYTES,COPIES
        budget.checkpoint(additional_memory=len(records)*ROW_BYTES*COPIES)
    records=consolidate(records,as_of_utc=as_of_utc,expected_native_indices=expected_native_indices)
    if records.empty: raise ValueError('Daily summaries require an explicitly represented native grid.')
    times=pd.to_datetime(records.VALID_TIME_UTC,utc=True)
    if any((t.minute,t.second,t.microsecond)!=(0,0,0) for t in times):
        raise ValueError('Daily source slots must be whole UTC hours.')
    if (times<start).any() or (times>end).any():
        raise ValueError('Pass only this day and its next-midnight boundary; do not load full history.')
    results=[]
    for index,group in records.groupby('SOURCE_GRID_INDEX',sort=True):
        if budget is not None:budget.checkpoint()
        if any(group[col].nunique()!=1 for col in ('SOURCE_LAT','SOURCE_LON','SOURCE_GRID_HASH')):
            raise ValueError('Native point coordinates change inside daily chunk.')
        group=group.copy()
        group['time']=pd.to_datetime(group.VALID_TIME_UTC,utc=True)
        values=group.pivot(index='time',columns='PARAMETER',values='VALUE')
        instants=pd.date_range(start,periods=24,freq='h')
        instant=values.reindex(instants)
        for param in ('t2m','d2m','u10','v10','msl','tcc'):
            if param not in instant: instant[param]=np.nan
        instant['t2m']-=273.15
        instant['d2m']-=273.15
        # Bolton saturation-over-liquid-water ratio, pinned coefficients; never clip
        # a supersaturated or malformed value into an apparently valid observation.
        t,td=instant.t2m,instant.d2m
        rh=100*np.exp(17.67*td/(td+243.5)-17.67*t/(t+243.5))
        if (rh.dropna()>100.01).any() or (td.dropna()<-123.15).any():
            raise ValueError('Dewpoint/temperature pair exceeds supported water-RH contract.')
        instant['rh']=rh
        instant['speed']=np.hypot(instant.u10,instant.v10)
        instant['msl']/=100
        instant['tcc']*=100
        precip=group[group.PARAMETER=='tp'].set_index('time')
        if not precip.empty and any(pd.Timestamp(row.INTERVAL_START_UTC)!=stamp-timedelta(hours=1)
                                    for stamp,row in precip.iterrows()):
            raise ValueError('Precipitation interval start is not the hour before its end.')
        pseries=precip.VALUE.reindex(pd.date_range(start+timedelta(hours=1),periods=24,freq='h'))*1000
        row=dict(DATE=day, TIMEZONE='UTC', SOURCE_MODEL='ERA5', SOURCE_GRID_INDEX=int(index),
                 SOURCE_LAT=float(group.SOURCE_LAT.iloc[0]),SOURCE_LON=float(group.SOURCE_LON.iloc[0]),
                 SOURCE_GRID_HASH=group.SOURCE_GRID_HASH.iloc[0],METHOD=METHOD,
                 SPATIAL_BASIS='CDS_0.25_degree_distribution_grid_point_not_H3_downscaled',
                 EXPVERS_PRESENT=','.join(map(str,sorted(set(group.EXPVER)))),
                 CONSOLIDATION_STATUS='contains_preliminary_ERA5T' if 5 in set(group.EXPVER) else 'final',
                 RETRIEVED_AS_OF_UTC=max(group.RETRIEVED_AT_UTC),
                 SOURCE_SHA256_RECEIPTS=json.dumps(sorted(set(group.SOURCE_SHA256))),
                 AVAILABLE_AT_UTC=None, AVAILABILITY_POLICY='actual_historical_publication_unknown_retrieval_as_of_only')
        for metric,(param,aggregation,_) in METRICS.items():
            prefix='ERA5_'+metric
            series=pseries if param=='tp' else instant[param] if param else pd.Series(dtype=float)
            valid=series[np.isfinite(series)]
            count=len(valid)
            row[prefix]=float(valid.sum() if aggregation=='sum' else valid.mean()) if count==24 else None
            row[prefix+'_STATUS']='COMPLETE' if count==24 else 'PARTIAL' if count else 'UNAVAILABLE'
            row[prefix+'_VALID_HOURS']=count
            row[prefix+'_EXPECTED_HOURS']=24
            row[prefix+'_COVERAGE_FRACTION']=count/24
        results.append(row)
    if budget is not None:budget.checkpoint()
    return pd.DataFrame(results)
