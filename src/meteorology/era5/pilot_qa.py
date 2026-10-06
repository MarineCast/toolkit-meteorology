"""Independent small-pilot slot, count, daily-value and missingness checks."""
from datetime import datetime,timedelta,timezone
import math

import pandas as pd
import pyarrow.parquet as pq

from .resources import ROW_BYTES,COPIES


def validate_native_pilot(directory,*,budget):
    normalized=directory/'normalized-native.parquet'
    daily_path=directory/'native-daily.parquet'
    n=pq.ParquetFile(normalized).metadata.num_rows
    if n>9*338:raise ValueError('Pilot normalized row count exceeds two-expver allowance.')
    budget.checkpoint(additional_memory=n*ROW_BYTES*COPIES)
    records=pq.read_table(normalized).to_pandas()
    daily=pq.read_table(daily_path).to_pandas()
    start=datetime(2024,1,1,tzinfo=timezone.utc)
    instants=[start+timedelta(hours=h) for h in range(24)]
    rain=[start+timedelta(hours=h) for h in range(1,25)]
    slots={(p,t) for p in ('t2m','d2m','u10','v10','msl','tcc') for t in instants}|{('tp',start),*(('tp',t) for t in rain)}
    if len(daily)!=9 or daily.SOURCE_GRID_INDEX.duplicated().any():
        raise ValueError('Pilot daily grid/cardinality differs.')
    checked=complete=partial=0
    for index,group in records.groupby('SOURCE_GRID_INDEX'):
        if group.duplicated(['PARAMETER','VALID_TIME_UTC','EXPVER']).any():
            raise ValueError('Duplicate hourly source identity in independent QA.')
        # Explicit final-first selection, including final missing values.
        values={}
        for row in sorted(group.itertuples(),key=lambda row:row.EXPVER,reverse=True):
            key=(row.PARAMETER,pd.Timestamp(row.VALID_TIME_UTC).to_pydatetime())
            values[key]=float(row.VALUE) if pd.notna(row.VALUE) else math.nan
            if row.PARAMETER=='tp' and pd.Timestamp(row.INTERVAL_START_UTC).to_pydatetime()!=key[1]-timedelta(hours=1):
                raise ValueError('Independent precipitation interval check failed.')
        if set(values)!=slots:raise ValueError('Requested hourly slots are missing or unexpected.')
        series={}
        mapping={'t2m':('TEMPERATURE_2M_C',lambda x:x-273.15),
                 'd2m':('DEWPOINT_2M_C',lambda x:x-273.15),
                 'u10':('U_WIND_10M_MS',lambda x:x),'v10':('V_WIND_10M_MS',lambda x:x),
                 'msl':('MEAN_SEA_LEVEL_PRESSURE_HPA',lambda x:x/100),
                 'tcc':('TOTAL_CLOUD_COVER_PCT',lambda x:x*100)}
        for param,(metric,convert) in mapping.items():series[metric]=[convert(values[param,t]) for t in instants]
        series['WIND_SPEED_10M_MS']=[math.hypot(values['u10',t],values['v10',t]) for t in instants]
        series['RELATIVE_HUMIDITY_2M_PCT']=[]
        for t in instants:
            temp=values['t2m',t]-273.15;dew=values['d2m',t]-273.15
            series['RELATIVE_HUMIDITY_2M_PCT'].append(100*math.exp(17.67*dew/(dew+243.5)-17.67*temp/(temp+243.5)))
        series['PRECIPITATION_MM']=[values['tp',t]*1000 for t in rain]
        row=daily[daily.SOURCE_GRID_INDEX==index].iloc[0]
        for metric,samples in series.items():
            prefix='ERA5_'+metric
            finite=[x for x in samples if math.isfinite(x)];count=len(finite)
            status='COMPLETE' if count==24 else 'PARTIAL' if count else 'UNAVAILABLE'
            if (row[prefix+'_VALID_HOURS']!=count or row[prefix+'_EXPECTED_HOURS']!=24
                    or row[prefix+'_STATUS']!=status or not math.isclose(row[prefix+'_COVERAGE_FRACTION'],count/24,abs_tol=1e-12)):
                raise ValueError('Independent daily completeness check failed.')
            if count==24:
                expected=math.fsum(finite) if metric=='PRECIPITATION_MM' else math.fsum(finite)/24
                if not math.isclose(float(row[prefix]),expected,rel_tol=1e-10,abs_tol=1e-9):
                    raise ValueError('Independent hourly-to-daily numerical check failed.')
                complete+=1
            elif pd.notna(row[prefix]):raise ValueError('Incomplete daily value must remain null.')
            else:partial+=1
            checked+=1
        for metric in ('VISIBILITY_KM','WIND_GUST_SURFACE_MS'):
            prefix='ERA5_'+metric
            if pd.notna(row[prefix]) or row[prefix+'_STATUS']!='UNAVAILABLE' or row[prefix+'_VALID_HOURS']!=0:
                raise ValueError('Unavailable ERA5 metric acquired a value.')
    if checked!=81:raise ValueError('Independent QA did not cover all nine source points.')
    budget.checkpoint()
    return dict(status='INDEPENDENT_NATIVE_PILOT_QA_PASSED',native_points=9,
                requested_slots_per_point=169,checked_metric_rows=checked,
                complete_metric_rows=complete,partial_or_unavailable_metric_rows=partial,
                final_h3_eligible=False,period_complete=False)
