"""The supported API: offline acquisition, exact selection and product checks."""
import json
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from meteorology import ALLOWED_VARIABLES, check_weather, get_weather, validate_product
from meteorology import marine_reporting, regional
from meteorology.artifacts import checksum_path
from .test_coastal import FakeArchive


@pytest.fixture
def weather(tmp_path, monkeypatch):
    member = SimpleNamespace(cells=('b',), resolution=5, artifact_sha256='membership',
        mask_sha256='mask', mask_revision='revision', study_identity={'study_config': {
            'domain': {'bbox_wgs84': list(regional.DEFAULT_BBOX)},
            'time': {'start': '2009-01-01', 'end_exclusive': '2010-01-01'}}})
    monkeypatch.setattr(marine_reporting, 'load_reporting_membership', lambda _: member)
    monkeypatch.setattr(regional, 'Archive', FakeArchive)
    monkeypatch.setattr(regional, 'mapping', lambda *a: ([0, 1], ['a', 'b'], np.zeros(2), 'test'))
    for flag in ('invalid_rh', 'fail', 'missing_rain'):
        monkeypatch.setattr(FakeArchive, flag, False)
    return dict(study_config='study', output_dir=tmp_path/'out', cache_dir=tmp_path/'cache')


def test_primary_api_exact_cells_and_all_fields(weather):
    result = get_weather('2009-01-01', '2009-01-02', ALLOWED_VARIABLES, **weather)
    report = check_weather(result.catalog_path, study_config='study')
    assert report['valid'] and report['membership_verified']
    assert report['products']['daily']['rows'] == 2
    assert report['products']['hourly']['rows'] == 48
    assert report['products']['daily']['metrics'] == 14
    assert report['products']['hourly']['metrics'] == 10
    assert validate_product(result.catalog_path)['valid']
    daily = pq.read_table(result.daily_path)
    assert daily['H3_INDEX'].to_pylist() == ['b', 'b']
    assert daily['PRECIPITATION_MM'].to_pylist() == [300., 300.]
    catalog = json.loads(result.catalog_path.read_text())
    assert catalog['availability']['historical_available_at_utc'] is None
    assert catalog['source_method'] == regional.METHOD


def test_selected_families_fetch_only_dependencies(weather, monkeypatch):
    original = FakeArchive.cube
    parameters_seen = []
    def cube(self, day, parameters):
        parameters_seen.append(parameters)
        return original(self, day, parameters)
    monkeypatch.setattr(FakeArchive, 'cube', cube)
    result = get_weather('2009-01-01', '2009-01-01', ['wind', 'temperature'], **weather)
    assert parameters_seen == [['t2m', 'u10', 'v10']]
    assert check_weather(result.catalog_path)['products']['daily']['metrics'] == 8


@pytest.mark.parametrize('variables', [[], ['snowfall'], ['temperature', 'temperature'], 'wind'])
def test_bad_variables_fail_before_source(weather, monkeypatch, variables):
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('source opened'))
    with pytest.raises(ValueError):
        get_weather('2009-01-01', '2009-01-01', variables, **weather)


def test_dates_fail_before_source(weather, monkeypatch):
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('source opened'))
    for start, end in [('2008-12-31', '2009-01-01'), ('2009-01-02', '2009-01-01')]:
        with pytest.raises(ValueError):
            get_weather(start, end, **weather)


def test_validator_rejects_shifted_times_even_with_updated_hash(weather):
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    table = pq.read_table(result.hourly_path)
    stamps = table['DATETIME'].cast(pa.int64()).to_numpy() + 30 * 60 * 10**6
    table = table.set_column(1, 'DATETIME', pa.array(stamps, type=pa.timestamp('us', tz='UTC')))
    pq.write_table(table, result.hourly_path)
    catalog = json.loads(result.catalog_path.read_text())
    catalog['products']['hourly'].update(sha256=checksum_path(result.hourly_path),
                                       bytes=result.hourly_path.stat().st_size)
    result.catalog_path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match='off-grid UTC'):
        check_weather(result.catalog_path)


def test_validator_rejects_forged_units_and_status(weather):
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    original = json.loads(result.catalog_path.read_text())
    altered = json.loads(json.dumps(original))
    altered['products']['daily']['columns'][2]['units'] = 'K'
    result.catalog_path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match='units'):
        check_weather(result.catalog_path)
    original['products']['daily']['value_status_counts']['TEMPERATURE_2M_C_MEAN']['COMPLETE'] = 0
    result.catalog_path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match='status counts'):
        check_weather(result.catalog_path)


def test_source_frontier_precipitation_requires_explicit_partial_day(weather, monkeypatch):
    class Frontier(FakeArchive):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.times = self.times[:24]
        def cube(self, day, parameters, allow_missing_boundary=False):
            assert allow_missing_boundary
            cube = super().cube(day, parameters)
            cube['values'][:, -1, :] = np.nan
            return cube
    monkeypatch.setattr(regional, 'Archive', Frontier)
    with pytest.raises(ValueError, match='coverage'):
        get_weather('2009-01-01', '2009-01-01', ['precipitation'], **weather)
    result = get_weather('2009-01-01', '2009-01-01', ['precipitation'],
                         allow_incomplete_final_day=True, **weather)
    catalog = json.loads(result.catalog_path.read_text())
    assert catalog['incomplete_final_day']
    assert catalog['partial_metric_coverage'][0]['valid_hours'] == 23
    assert pq.read_table(result.daily_path)['PRECIPITATION_MM'].null_count == 1
    assert pq.read_table(result.hourly_path)['PRECIPITATION_MM'].null_count == 1
    assert check_weather(result.catalog_path)['valid']
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    catalog = json.loads(result.catalog_path.read_text())
    assert not catalog['incomplete_final_day']
    assert check_weather(result.catalog_path)['products']['hourly']['null_counts'] == {'TEMPERATURE_2M_C': 0}


def test_primary_imports_do_not_load_retired_stacks():
    import subprocess
    import sys
    code = """
import importlib.abc
import sys
class RejectLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pyproj', 'shapely', 'sklearn', 'xarray', 'psutil', 'pydantic', 'eccodes', 'cdsapi', 'herbie', 'geopandas', 'folium', 'branca'}:
            raise AssertionError('Retired dependency imported: ' + fullname)
sys.meta_path.insert(0, RejectLegacy())
import meteorology
from meteorology import regional, regional_source, coastal, marine_reporting
assert len(meteorology.ALLOWED_VARIABLES) == 7
assert not hasattr(meteorology, 'get_weather_request')
assert not hasattr(meteorology, 'get_temperature')
"""
    subprocess.run([sys.executable, '-c', code], check=True)


@pytest.mark.parametrize('verbose', [True, False])
def test_env_defaults_produce_offline_weather(weather, tmp_path, monkeypatch, capsys, verbose):
    from meteorology.run_settings import KEYS
    monkeypatch.chdir(tmp_path)
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    defaults = tmp_path/'.env'
    defaults.write_text(f'DATA_DIR={weather["output_dir"]}\nSTART_DATE=2009-01-01\n'
                        f'END_DATE=2009-01-01\nVERBOSITY={verbose}\n')
    result = get_weather(variable_list=['temperature'], study_config=weather['study_config'],
                         cache_dir=weather['cache_dir'])
    assert result.catalog_path.parent == weather['output_dir']
    assert check_weather(result.catalog_path)['products']['daily']['rows'] == 1
    logs = capsys.readouterr()
    assert not logs.out
    assert ('Processing UTC day 2009-01-01' in logs.err) == verbose
    assert ('Published ' in logs.err) == verbose
    result = get_weather('2009-01-02', '2009-01-02', ['temperature'],
                         verbosity=False, output_dir=tmp_path/'override',
                         study_config=weather['study_config'], cache_dir=weather['cache_dir'])
    assert result.catalog_path.parent == tmp_path/'override'
    assert json.loads(result.catalog_path.read_text())['start_date'] == '2009-01-02'
    assert not capsys.readouterr().err
