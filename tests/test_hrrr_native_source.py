import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from meteorology.hourly_weather.native_source import decode_hour,FIELD_HEADERS
from meteorology.era5.resources import Budget,Limits

VALUES={'temperature_2m_k':283.15,'relative_humidity_2m_pct':50.,'u_wind_10m_ms':3.,'v_wind_10m_ms':4.,
        'wind_gust_surface_ms':8.,'visibility_m':5000.,'total_cloud_cover_pct':50.,'mean_sea_level_pressure_pa':101325.}


def gribs(tmp_path,*,bad_units=False,bad_hour=False,bitmap=False,grid_wind=False):
    ec=pytest.importorskip('eccodes');files={};bbox=None
    for name,(discipline,category,parameter,surfaces,level) in FIELD_HEADERS.items():
        path=tmp_path/(name+'.grib2');files[name]=path
        m=ec.codes_grib_new_from_samples('regular_ll_sfc_grib2')
        try:
            for key,value in dict(centre='kwbc',gridType='lambert',Nx=4,Ny=4,
                latitudeOfFirstGridPointInDegrees=49.,longitudeOfFirstGridPointInDegrees=235.,
                LoVInDegrees=265.,Latin1InDegrees=25.,Latin2InDegrees=25.,DxInMetres=3000.,DyInMetres=3000.,
                iScansNegatively=0,jScansPositively=1,jPointsAreConsecutive=0,alternativeRowScanning=0,
                dataDate=20240101,dataTime=100 if bad_hour and name=='temperature_2m_k' else 0,
                discipline=discipline,parameterCategory=category,parameterNumber=parameter,
                typeOfFirstFixedSurface=200 if name=='total_cloud_cover_pct' else min(surfaces),
                typeOfSecondFixedSurface=255,scaleFactorOfFirstFixedSurface=0,scaledValueOfFirstFixedSurface=level,
                stepType='instant',startStep=0,endStep=0).items():ec.codes_set(m,key,value)
            if name in ('u_wind_10m_ms','v_wind_10m_ms'):ec.codes_set(m,'uvRelativeToGrid',1 if grid_wind else 0)
            vals=[VALUES[name]]*16
            if bitmap and name=='temperature_2m_k':
                ec.codes_set(m,'bitmapPresent',1);ec.codes_set(m,'missingValue',9999);vals[0]=9999
            ec.codes_set_values(m,vals)
            lat=np.asarray(ec.codes_get_array(m,'latitudes'));lon=np.asarray(ec.codes_get_array(m,'longitudes'))-360
            midlat=float(lat.mean());midlon=float(lon.mean());bbox=[midlon-.001,midlat-.001,midlon+.001,midlat+.001]
            with path.open('wb') as stream:ec.codes_write(m,stream)
        finally:ec.codes_release(m)
    return files,bbox


def decode(files,bbox):
    return decode_hour(files,valid=pd.Timestamp('2024-01-01T00:00:00Z'),bbox=bbox,halo=.2,
        budget=Budget(Limits(memory_bytes=1024**3),rss=lambda:0),expected_shape=(4,4))


def test_real_grib2_fields_units_native_footprint_and_bitmap(tmp_path):
    files,bbox=gribs(tmp_path,bitmap=True)
    frame,evidence=decode(files,bbox)
    assert len(frame)==16 and frame.TEMPERATURE_2M_C.isna().sum()==1
    assert frame.WIND_SPEED_10M_MS.tolist()==pytest.approx([5]*16)
    assert frame.MEAN_SEA_LEVEL_PRESSURE_HPA.tolist()==pytest.approx([1013.25]*16)
    assert frame.VISIBILITY_KM.tolist()==pytest.approx([5]*16)
    assert evidence['max_nearest_distance_m']>0 and evidence['native_footprint_wkb_hex']
    assert evidence['source_evidence_kind']=='retained_decoded_hrrr'


def test_grid_relative_rotation_preserves_magnitude(tmp_path):
    files,bbox=gribs(tmp_path,grid_wind=True)
    frame,evidence=decode(files,bbox)
    assert evidence['source_wind_basis']=='grid_relative'
    assert frame.WIND_SPEED_10M_MS.tolist()==pytest.approx([5]*16)
    assert not np.allclose(frame.U_WIND_10M_MS,3)


def test_wrong_utc_header_and_real_model_shape_rejected(tmp_path):
    files,bbox=gribs(tmp_path,bad_hour=True)
    with pytest.raises(ValueError,match='initial/valid time'):decode(files,bbox)
    files,bbox=gribs(tmp_path)
    with pytest.raises(ValueError,match='native grid differs'):
        decode_hour(files,valid=pd.Timestamp('2024-01-01T00:00:00Z'),bbox=bbox,halo=.2,budget=Budget(Limits(),rss=lambda:0))


def test_duplicate_selected_message_rejected(tmp_path):
    files,bbox=gribs(tmp_path)
    path=files['temperature_2m_k'];path.write_bytes(path.read_bytes()*2)
    with pytest.raises(ValueError,match='multiple GRIB'):decode(files,bbox)


def test_maps_pressure_identity_is_198_and_eta_192_is_rejected(tmp_path):
    # NOAA table 4.2-0-3: MSLMA=198, MSLET=192. Same units do not permit substitution.
    ec=pytest.importorskip('eccodes');files,bbox=gribs(tmp_path)
    pressure=files['mean_sea_level_pressure_pa']
    with pressure.open('rb') as stream:m=ec.codes_grib_new_from_file(stream)
    try:
        assert ec.codes_get_long(m,'parameterNumber')==198
        ec.codes_set(m,'parameterNumber',192)
        with pressure.open('wb') as stream:ec.codes_write(m,stream)
    finally:ec.codes_release(m)
    with pytest.raises(ValueError,match='field/level/centre'):
        decode(files,bbox)
