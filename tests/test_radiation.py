"""Independent radiation interval arithmetic, vintage retention and failure gates."""
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from meteorology import check_solar_radiation, get_solar_radiation
from meteorology import radiation
from meteorology.artifacts import checksum_path
from meteorology.regional_source import Archive
from .test_marine_reporting import registry
from .test_weather_api import weather as _weather_fixture


class RadiationArchive:
    end = '2009-01-13T00:00:00Z'
    version = 1
    fail_day = None
    invalid_units = False
    requests_seen = []

    def __init__(self, root, bbox, **options):
        assert options['parameter_units'] == {'ssrd': 'J m**-2', 'fdir': 'J m**-2'}
        self.times = pd.date_range('2009-01-01T00:00:00Z', self.end, freq='h', tz='UTC').as_unit('s').asi8
        self.coords = np.array([[49., -125.], [50., -126.]])
        self.snapshot = 'synthetic-vintage-'+str(self.version)
        self.requests = self.bytes = 0
        self.used = {}
        self.blocks = {}
        self.meta = {p+'/.zattrs': dict(units='bad' if self.invalid_units else 'J m**-2',
            GRIB_paramId=pid, GRIB_stepType='accum') for p, pid in [('ssrd', 169), ('fdir', 228021)]}
        (Path(root)/'objects').mkdir(exist_ok=True)
        (Path(root)/'objects'/'synthetic-source').write_text('task-owned fixture')

    def checkpoint(self):
        pass

    def cube(self, day, parameters, *, allow_missing_boundary=False):
        if str(day) == self.fail_day:
            raise RuntimeError('injected source failure')
        assert parameters == ['ssrd', 'fdir']
        self.requests_seen.append(str(day))
        stamps = pd.date_range(day, periods=25, freq='h', tz='UTC').as_unit('s')
        positions = (stamps.asi8-self.times[0])//3600
        missing = stamps.asi8 > self.times[-1]
        if missing.any() and not (allow_missing_boundary and missing.sum() == 1 and missing[-1]):
            raise ValueError('fixture source boundary unavailable')
        energy = positions[:, None]*3600*self.version + np.array([0, 100])[None, :]
        values = np.array([energy, energy*.25], dtype=float)
        values[:, missing] = np.nan
        return dict(values=values)


@pytest.fixture
def selection(tmp_path, monkeypatch):
    member, study, _, _ = registry(tmp_path)
    monkeypatch.setattr(radiation, 'load_reporting_membership', lambda _: member)
    monkeypatch.setattr(radiation, 'Archive', RadiationArchive)
    monkeypatch.setattr(radiation, '_spatial', lambda *args: (
        np.arange(len(member.cells)) % 2, np.zeros(len(member.cells)), 'synthetic-test-only'))
    for key, value in dict(end='2009-01-13T00:00:00Z', version=1, fail_day=None,
                           invalid_units=False, requests_seen=[]).items():
        monkeypatch.setattr(RadiationArchive, key, value)
    return member, dict(study_config=study, output_dir=tmp_path/'solar-radiation')


@pytest.fixture
def weather_options(tmp_path, monkeypatch):
    return _weather_fixture.__wrapped__(tmp_path, monkeypatch)


def test_native_interval_end_energy_flux_and_complete_daily_arithmetic(selection):
    member, options = selection
    result = get_solar_radiation('2009-01-01', '2009-01-01', **options)
    hourly = pq.read_table(result.hourly_path).to_pandas()
    daily = pq.read_table(result.daily_path).to_pandas()
    first = hourly[hourly.H3_INDEX.eq(member.cells[0])]
    assert first.DATETIME.iloc[0] == pd.Timestamp('2009-01-01T00:00:00Z')
    assert first.DOWNWARD_SHORTWAVE_ENERGY_J_M2.iloc[0] == 3600
    assert first.DOWNWARD_SHORTWAVE_ENERGY_J_M2.iloc[-1] == 24*3600
    assert first.DOWNWARD_SHORTWAVE_IRRADIANCE_W_M2.tolist() == list(range(1, 25))
    assert daily.DOWNWARD_SHORTWAVE_ENERGY_J_M2.iloc[0] == sum(range(1, 25))*3600
    assert daily.DOWNWARD_SHORTWAVE_IRRADIANCE_W_M2_MEAN.iloc[0] == 12.5
    assert daily.DIRECT_SOLAR_IRRADIANCE_W_M2_MEAN.iloc[0] == 12.5*.25
    report = check_solar_radiation(result.catalog_path, study_config='study')
    assert report['valid'] and report['membership_verified']
    assert report['products']['hourly']['rows'] == 24*len(member.cells)
    assert report['quality']['partial_days'] == []


def test_actual_frontier_requires_opt_in_and_keeps_partial_day_null(selection, monkeypatch):
    member, options = selection
    monkeypatch.setattr(RadiationArchive, 'end', '2009-01-03T23:00:00Z')
    result = get_solar_radiation(**options)
    assert check_solar_radiation(result.catalog_path)['end_date'] == '2009-01-02'
    with pytest.raises(ValueError, match='source support'):
        get_solar_radiation('2009-01-01', '2009-01-03', **options)
    get_solar_radiation(allow_incomplete_final_day=True, **options)
    report = check_solar_radiation(result.catalog_path)
    assert report['end_date'] == '2009-01-03'
    assert report['products']['hourly']['null_counts']['DIRECT_SOLAR_ENERGY_J_M2'] == len(member.cells)
    last = pq.ParquetFile(result.daily_path).read_row_group(2)
    assert last['DIRECT_SOLAR_ENERGY_J_M2'].null_count == len(member.cells)
    assert report['quality']['partial_days'][0]['minimum_valid_hours'] == 23
    monkeypatch.setattr(RadiationArchive, 'end', '2009-01-06T22:00:00Z')
    with pytest.raises(ValueError, match='source support'):
        get_solar_radiation('2009-01-06', '2009-01-06', allow_incomplete_final_day=True, **options)


def test_noop_append_bridge_preserves_vintage_and_explicit_rebuild(selection, monkeypatch):
    member, options = selection
    result = get_solar_radiation('2009-01-02', '2009-01-02', **options)
    before = {p.name: checksum_path(p) for p in options['output_dir'].iterdir()}
    original = pq.read_table(result.hourly_path)
    monkeypatch.setattr(radiation, 'Archive', lambda *a, **k: pytest.fail('covered request acquired source'))
    get_solar_radiation('2009-01-02', '2009-01-02', **options)
    assert before == {p.name: checksum_path(p) for p in options['output_dir'].iterdir()}
    monkeypatch.setattr(radiation, 'Archive', RadiationArchive)
    monkeypatch.setattr(RadiationArchive, 'version', 2)
    get_solar_radiation('2009-01-05', '2009-01-05', **options)
    get_solar_radiation('2009-01-01', '2009-01-01', **options)
    actual = pq.ParquetFile(result.hourly_path).read_row_group(1)
    assert actual.equals(original, check_metadata=False)
    catalog = json.loads(result.catalog_path.read_text())
    assert catalog['start_date'] == '2009-01-01' and catalog['end_date'] == '2009-01-05'
    assert len(catalog['source_epochs']) == 3
    assert check_solar_radiation(result.catalog_path)['products']['daily']['rows'] == len(member.cells)*5
    get_solar_radiation('2009-01-02', '2009-01-02', rebuild=True, **options)
    assert check_solar_radiation(result.catalog_path)['products']['daily']['rows'] == len(member.cells)
    assert pq.read_table(result.hourly_path)['DOWNWARD_SHORTWAVE_ENERGY_J_M2'][0].as_py() == 25*3600*2


def test_existing_partial_values_are_retained_on_later_append(selection, monkeypatch):
    member, options = selection
    monkeypatch.setattr(RadiationArchive, 'end', '2009-01-02T23:00:00Z')
    result = get_solar_radiation(allow_incomplete_final_day=True, **options)
    before = pq.ParquetFile(result.hourly_path).read_row_group(1)
    monkeypatch.setattr(RadiationArchive, 'end', '2009-01-04T00:00:00Z')
    get_solar_radiation(**options)
    assert pq.ParquetFile(result.hourly_path).read_row_group(1).equals(before, check_metadata=False)
    assert check_solar_radiation(result.catalog_path)['products']['hourly']['null_counts']['DIRECT_SOLAR_ENERGY_J_M2'] == len(member.cells)


def test_source_metadata_gate_and_failed_acquisition_preserve_delivery(selection, monkeypatch):
    _, options = selection
    get_solar_radiation('2009-01-01', '2009-01-01', **options)
    before = {p.name: checksum_path(p) for p in options['output_dir'].iterdir()}
    monkeypatch.setattr(RadiationArchive, 'invalid_units', True)
    with pytest.raises(ValueError, match='source identity'):
        get_solar_radiation('2009-01-02', '2009-01-02', **options)
    monkeypatch.setattr(RadiationArchive, 'invalid_units', False)
    monkeypatch.setattr(RadiationArchive, 'fail_day', '2009-01-02')
    with pytest.raises(RuntimeError, match='injected source'):
        get_solar_radiation('2009-01-02', '2009-01-02', **options)
    assert before == {p.name: checksum_path(p) for p in options['output_dir'].iterdir()}


def test_checker_detects_arithmetic_corruption_with_updated_file_hash(selection):
    _, options = selection
    result = get_solar_radiation('2009-01-01', '2009-01-01', **options)
    table = pq.read_table(result.hourly_path)
    name = 'DIRECT_SOLAR_IRRADIANCE_W_M2'
    table = table.set_column(table.column_names.index(name), name, pa.array(table[name].to_numpy()+1))
    pq.write_table(table, result.hourly_path)
    catalog = json.loads(result.catalog_path.read_text())
    catalog['products']['hourly'].update(sha256=checksum_path(result.hourly_path), bytes=result.hourly_path.stat().st_size)
    result.catalog_path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match='reduction mismatch'):
        check_solar_radiation(result.catalog_path)


def test_partial_sum_cannot_masquerade_as_daily_total(selection, monkeypatch):
    _, options = selection
    monkeypatch.setattr(RadiationArchive, 'end', '2009-01-01T23:00:00Z')
    result = get_solar_radiation(allow_incomplete_final_day=True, **options)
    table = pq.read_table(result.daily_path)
    name = 'DOWNWARD_SHORTWAVE_ENERGY_J_M2'
    table = table.set_column(table.column_names.index(name), name, pa.array([0.]*len(table)))
    pq.write_table(table, result.daily_path)
    catalog = json.loads(result.catalog_path.read_text())
    catalog['products']['daily'].update(sha256=checksum_path(result.daily_path), bytes=result.daily_path.stat().st_size)
    result.catalog_path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match='reduction mismatch'):
        check_solar_radiation(result.catalog_path)


@pytest.mark.parametrize('start,end', [('2008-12-31','2009-01-01'), ('2009-01-02','2009-01-01'),
                                     ('2009-02-30','2009-03-01'), ('2027-01-01','2027-01-02')])
def test_invalid_dates_do_not_acquire(selection, monkeypatch, start, end):
    _, options = selection
    monkeypatch.setattr(radiation, 'Archive', lambda *a, **k: pytest.fail('invalid dates acquired'))
    with pytest.raises(ValueError):
        get_solar_radiation(start, end, **options)


def test_cache_ownership_and_unrelated_output_are_preserved(selection, tmp_path):
    _, options = selection
    options['output_dir'].mkdir()
    (options['output_dir']/'keep.txt').write_text('user file')
    with pytest.raises(ValueError, match='unrelated'):
        get_solar_radiation('2009-01-01', '2009-01-01', **options)
    cache = tmp_path/'shared'
    cache.mkdir()
    (cache/'keep.txt').write_text('shared input')
    with pytest.raises(ValueError, match='ownership'):
        radiation._clean_cache(cache, options['output_dir'])
    assert (cache/'keep.txt').read_text() == 'shared input'


def test_both_companions_survive_weather_append_and_rebuild(selection, weather_options, tmp_path, monkeypatch):
    from meteorology import get_weather, check_weather, solar
    member, options = selection
    monkeypatch.chdir(tmp_path)
    weather = weather_options
    get_weather('2009-01-01', '2009-01-01', **weather)
    rad = get_solar_radiation('2009-01-01', '2009-01-01', **{**options, 'output_dir': weather['output_dir']/'solar-radiation'})
    original = checksum_path(rad.catalog_path)
    monkeypatch.setattr(solar, 'load_reporting_membership', lambda _: member)
    sun = solar.get_solar_cycle('2009-01-01', '2009-01-01', study_config=options['study_config'],
                               output_dir=weather['output_dir']/'solar-cycle')
    solar_original = checksum_path(sun.catalog_path)
    get_weather('2009-01-02', '2009-01-02', **{**weather, 'rebuild': False})
    get_weather('2009-01-01', '2009-01-01', **weather)
    assert check_weather(weather['output_dir']/'catalog.json')['valid']
    assert checksum_path(rad.catalog_path) == original
    assert checksum_path(sun.catalog_path) == solar_original
    assert not solar.is_solar_directory(rad.catalog_path.parent)


def test_archive_rejects_bad_parameter_contract_before_io(tmp_path):
    for parameters in ({}, {'ssrd':'W m-2'}, {'unknown':'K'}):
        with pytest.raises(ValueError, match='parameter contract'):
            Archive(tmp_path/'absent', [-129,46,-123,50], parameter_units=parameters)
    assert not (tmp_path/'absent').exists()


def test_radiation_cli_visible():
    result = subprocess.run([sys.executable, '-m', 'meteorology', 'radiation', '--help'],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert '--allow-incomplete-final-day' in result.stdout


def test_native_zero_signed_roundoff_and_independent_missingness(selection, monkeypatch):
    _, options = selection
    original = RadiationArchive.cube
    def cube(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        result['values'][0, 1, 0] = 0
        result['values'][1, 2, 0] = -.01
        result['values'][0, 3, 0] = np.nan
        return result
    monkeypatch.setattr(RadiationArchive, 'cube', cube)
    result = get_solar_radiation('2009-01-01', '2009-01-01', **options)
    hourly = pq.read_table(result.hourly_path)
    daily = pq.read_table(result.daily_path)
    assert hourly['DOWNWARD_SHORTWAVE_ENERGY_J_M2'][0].as_py() == 0
    assert hourly['DIRECT_SOLAR_ENERGY_J_M2'][len(daily)].as_py() == -.01
    assert daily['DOWNWARD_SHORTWAVE_ENERGY_J_M2'][0].as_py() is None
    assert daily['DIRECT_SOLAR_ENERGY_J_M2'][0].as_py() is not None
    assert check_solar_radiation(result.catalog_path)['quality']['negative_hourly_energy']['fdir'] > 0


@pytest.mark.parametrize('companions_first', [True, False])
def test_weather_initialization_and_refresh_preserve_both_companions(
        selection, weather_options, tmp_path, monkeypatch, companions_first):
    from meteorology import get_weather, solar, refresh, marine_reporting
    member, options = selection
    monkeypatch.setattr(solar, 'load_reporting_membership', lambda _: member)
    weather_member = marine_reporting.load_reporting_membership('study')
    monkeypatch.setattr(refresh, 'load_reporting_membership', lambda _: weather_member)
    output = weather_options['output_dir']
    weather_options['rebuild'] = False
    if not companions_first:
        get_weather('2009-01-01', '2009-01-01', **weather_options)
    solar.get_solar_cycle('2009-01-01', '2009-01-01', study_config=options['study_config'],
                          output_dir=output/'solar-cycle')
    get_solar_radiation('2009-01-01', '2009-01-01',
                        **{**options, 'output_dir': output/'solar-radiation'})
    before = {p: p.read_bytes() for child in ('solar-cycle', 'solar-radiation')
              for p in (output/child).iterdir()}
    get_weather('2009-01-01', '2009-01-01', **weather_options)
    refresh.refresh_weather('2009-01-02', study_config='study', output_dir=output,
                            cache_dir=tmp_path/'refresh-cache')
    assert all(p.read_bytes() == content for p, content in before.items())
    assert json.loads((output/'catalog.json').read_text())['end_date'] == '2009-01-02'
    (output/'unrelated.txt').write_text('preserve')
    with pytest.raises(ValueError, match='complete weather'):
        refresh.refresh_weather('2009-01-03', study_config='study', output_dir=output,
                                cache_dir=tmp_path/'refresh-cache')
    assert (output/'unrelated.txt').read_text() == 'preserve'


def test_radiation_noop_finishes_failed_source_cleanup(selection, monkeypatch):
    _, options = selection
    original = radiation._clean_cache
    caches = []
    def fail(cache, output):
        caches.append(cache)
        raise PermissionError('injected source cleanup failure')
    monkeypatch.setattr(radiation, '_clean_cache', fail)
    with pytest.raises(PermissionError, match='cleanup failure'):
        get_solar_radiation('2009-01-01', '2009-01-01', **options)
    assert (caches[0]/'objects').exists()
    monkeypatch.setattr(radiation, '_clean_cache', original)
    monkeypatch.setattr(radiation, 'Archive', lambda *a, **k: pytest.fail('source accessed'))
    result = get_solar_radiation('2009-01-01', '2009-01-01', **options)
    assert check_solar_radiation(result.catalog_path)['valid']
    assert not (caches[0]/'objects').exists()
