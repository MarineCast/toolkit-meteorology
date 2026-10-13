"""Small generated GRIB source fixtures, never captured provider data."""
from pathlib import Path
import numpy as np
import pandas as pd


def era5_grib(path, *, day='2024-01-01', parameters=('t2m',), area=(49.5,-125.5,48.75,-124.75),
              missing_hour=None, snow=.001, rain=0., expver=1):
    import eccodes as ec
    from meteorology.era5.source import PARAMETERS
    ids = {name:param for param,(name,_) in PARAMETERS.items()}
    samples = {'t2m':283.15,'d2m':278.15,'u10':3.,'v10':4.,'msl':101325.,'tcc':.5,'tp':rain,'sf':snow}
    ny = round((area[0]-area[2])*4)+1
    nx = round((area[3]-area[1])*4)+1
    with Path(path).open('wb') as output:
        for hour in range(25 if set(parameters)&{'tp','sf'} else 24):
            for parameter in parameters:
                if hour==24 and parameter not in {'tp','sf'}:
                    continue
                valid = pd.Timestamp(day,tz='UTC')+pd.Timedelta(hours=hour)
                accum = parameter in {'tp','sf'}
                init = valid-pd.Timedelta(hours=1) if accum else valid
                message = ec.codes_grib_new_from_samples('regular_ll_sfc_grib1')
                try:
                    for key,value in {'class':'ea','type':'fc' if accum else 'an','paramId':ids[parameter],
                        'Ni':nx,'Nj':ny,'latitudeOfFirstGridPointInDegrees':area[0],
                        'latitudeOfLastGridPointInDegrees':area[2],'longitudeOfFirstGridPointInDegrees':area[1]%360,
                        'longitudeOfLastGridPointInDegrees':area[3]%360,
                        'iDirectionIncrementInDegrees':.25,'jDirectionIncrementInDegrees':.25,
                        'dataDate':int(init.strftime('%Y%m%d')),'dataTime':int(init.strftime('%H%M')),
                        'stepType':'accum' if accum else 'instant','startStep':0,'endStep':1 if accum else 0,
                        'experimentVersionNumber':f'{expver:04}'}.items():
                        ec.codes_set(message,key,value)
                    if parameter in {'u10','v10'}:
                        ec.codes_set(message,'uvRelativeToGrid',0)
                    values = np.full(nx*ny,samples[parameter])
                    if hour==missing_hour:
                        ec.codes_set(message,'bitmapPresent',1)
                        ec.codes_set(message,'missingValue',9999)
                        values[:] = 9999
                    ec.codes_set_values(message,values)
                    ec.codes_write(message,output)
                finally:
                    ec.codes_release(message)
    return Path(path)
