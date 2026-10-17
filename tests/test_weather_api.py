"""The supported API: offline acquisition, exact selection and product checks."""
import json
from datetime import date
from pathlib import Path
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
    return dict(study_config='study', output_dir=tmp_path/'out', cache_dir=tmp_path/'cache', rebuild=True)


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
                        f'END_DATE=2009-01-01\nVERBOSITY={verbose}\nMETRICS=temperature\nREBUILD=True\n')
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


@pytest.mark.parametrize('rebuild', [False, True])
def test_both_modes_build_absent_output(weather, rebuild):
    weather['rebuild'] = rebuild
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    assert check_weather(result.catalog_path)['products']['hourly']['rows'] == 24


def test_append_covered_range_is_verified_noop(weather, monkeypatch):
    result = get_weather('2009-01-01', '2009-01-02', ['temperature'], **weather)
    original = {p: p.read_bytes() for p in (result.catalog_path, result.daily_path, result.hourly_path)}
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('source opened'))
    weather['rebuild'] = False
    assert get_weather('2009-01-02', '2009-01-02', ['temperature'], **weather) == result
    assert all(p.read_bytes() == value for p, value in original.items())


@pytest.mark.parametrize('initial,requested,expected', [
    (('2009-01-01', '2009-01-01'), ('2009-01-01', '2009-01-03'), [2, 3]),
    (('2009-01-03', '2009-01-03'), ('2009-01-01', '2009-01-02'), [1, 2]),
    (('2009-01-02', '2009-01-02'), ('2009-01-01', '2009-01-03'), [1, 3]),
    (('2009-01-01', '2009-01-01'), ('2009-01-03', '2009-01-03'), [2, 3]),
])
def test_append_only_acquires_missing_dates_and_preserves_old(weather, monkeypatch,
                                                              initial, requested, expected):
    result = get_weather(*initial, ['temperature'], **weather)
    old = pq.read_table(result.daily_path).to_pydict()
    seen = []
    original = FakeArchive.cube
    def cube(self, day, parameters):
        seen.append(day.day)
        cube = original(self, day, parameters)
        cube['values'] += 10  # a different source vintage must not change saved days
        return cube
    monkeypatch.setattr(FakeArchive, 'cube', cube)
    weather['rebuild'] = False
    get_weather(*requested, ['temperature'], **weather)
    assert seen == expected
    assert check_weather(result.catalog_path, study_config='study')['products']['daily']['rows'] == 3
    table = pq.read_table(result.daily_path).to_pandas()
    for day, value in zip(old['DATE'], old['TEMPERATURE_2M_C_MEAN']):
        assert table.loc[table.DATE == day, 'TEMPERATURE_2M_C_MEAN'].iloc[0] == value
    catalog = json.loads(result.catalog_path.read_text())
    assert catalog['source_epochs']
    assert 'source_metadata_sha256' not in catalog
    assert sum((date.fromisoformat(e['end_date']) -
                date.fromisoformat(e['start_date'])).days + 1
               for e in catalog['source_epochs']) == 3
    # A later no-op can read multi-epoch products without requiring single-vintage hashes.
    get_weather(*requested, ['temperature'], **weather)
    assert seen == expected


def test_rebuild_replaces_range_and_metrics(weather):
    result = get_weather('2009-01-01', '2009-01-03', ['temperature'], **weather)
    get_weather('2009-01-02', '2009-01-02', ['pressure'], **weather)
    report = check_weather(result.catalog_path)
    assert report['start_date'] == report['end_date'] == '2009-01-02'
    assert report['products']['daily']['metrics'] == 2
    assert 'source_epochs' not in json.loads(result.catalog_path.read_text())


def test_append_rejects_corruption_and_unrelated_files(weather, monkeypatch):
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('source opened'))
    weather['rebuild'] = False
    with result.hourly_path.open('ab') as f:
        f.write(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        get_weather('2009-01-02', '2009-01-02', ['temperature'], **weather)
    marker = result.catalog_path.parent/'user.txt'
    marker.write_text('keep me')
    weather['rebuild'] = True
    with pytest.raises(ValueError, match='unrelated'):
        get_weather('2009-01-02', '2009-01-02', ['temperature'], **weather)
    assert marker.read_text() == 'keep me'


def test_append_failure_keeps_all_previous_files(weather, monkeypatch):
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    previous = {p: p.read_bytes() for p in (result.catalog_path, result.daily_path, result.hourly_path)}
    weather['rebuild'] = False
    monkeypatch.setattr(FakeArchive, 'fail', True)
    with pytest.raises(RuntimeError, match='Simulated'):
        get_weather('2009-01-01', '2009-01-03', ['temperature'], **weather)
    assert all(p.read_bytes() == value for p, value in previous.items())


def test_repeated_append_preserves_null_status_and_flattens_epochs(weather, monkeypatch):
    monkeypatch.setattr(FakeArchive, 'invalid_rh', True)
    result = get_weather('2009-01-01', '2009-01-01', ALLOWED_VARIABLES, **weather)
    weather['rebuild'] = False
    get_weather('2009-01-02', '2009-01-02', ALLOWED_VARIABLES, **weather)
    get_weather('2009-01-03', '2009-01-03', ALLOWED_VARIABLES, **weather)
    report = check_weather(result.catalog_path, study_config='study')
    assert report['products']['daily']['rows'] == 3
    assert len(json.loads(result.catalog_path.read_text())['source_epochs']) == 3


def test_incomplete_directory_requires_explicit_rebuild(weather):
    weather['output_dir'].mkdir()
    (weather['output_dir']/'meteorology-hourly.parquet').write_bytes(b'partial')
    weather['rebuild'] = False
    with pytest.raises(ValueError, match='Incomplete'):
        get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    weather['rebuild'] = True
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    assert check_weather(result.catalog_path)['valid']



def test_partial_null_day_is_retained_when_later_days_append(weather, monkeypatch):
    class Frontier(FakeArchive):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.times = self.times[:24]
        def cube(self, day, parameters, allow_missing_boundary=False):
            cube = super().cube(day, parameters)
            cube['values'][:, -1, :] = np.nan
            return cube
    monkeypatch.setattr(regional, 'Archive', Frontier)
    result = get_weather('2009-01-01', '2009-01-01', ['precipitation'],
                         allow_incomplete_final_day=True, **weather)
    monkeypatch.setattr(regional, 'Archive', FakeArchive)
    weather['rebuild'] = False
    get_weather('2009-01-02', '2009-01-02', ['precipitation'], **weather)
    report = check_weather(result.catalog_path)
    assert report['products']['daily']['null_counts']['PRECIPITATION_MM'] == 1
    catalog = json.loads(result.catalog_path.read_text())
    assert not catalog['incomplete_final_day']
    assert catalog['partial_metric_coverage'][0]['date'] == '2009-01-01'
    assert catalog['latest_complete_all_metric_day'] == '2009-01-02'


def test_append_rollback_and_retry(weather, monkeypatch):
    from meteorology.core.artifacts import publication
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    previous = {p: p.read_bytes() for p in (result.catalog_path, result.daily_path, result.hourly_path)}
    original_replace = publication.os.replace
    failed = False
    def fail_daily(source, destination):
        nonlocal failed
        if Path(destination) == result.daily_path and not failed:
            failed = True
            raise OSError('append publication failure')
        return original_replace(source, destination)
    weather['rebuild'] = False
    monkeypatch.setattr(publication.os, 'replace', fail_daily)
    with pytest.raises(OSError, match='append publication failure'):
        get_weather('2009-01-02', '2009-01-02', ['temperature'], **weather)
    assert all(p.read_bytes() == value for p, value in previous.items())
    get_weather('2009-01-02', '2009-01-02', ['temperature'], **weather)
    assert check_weather(result.catalog_path)['products']['daily']['rows'] == 2


def test_append_lock_rejects_concurrent_writer(weather, monkeypatch):
    import hashlib
    from meteorology.core.artifacts import TransactionalFamilyPublisher
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    output = result.catalog_path.parent
    control = output.parent / ('.meteorology-publication-' +
                               hashlib.sha256(str(output).encode()).hexdigest()[:16])
    weather['rebuild'] = False
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('source opened'))
    with TransactionalFamilyPublisher(control):
        with pytest.raises(RuntimeError, match='busy'):
            get_weather('2009-01-02', '2009-01-02', ['temperature'], **weather)


def test_env_metrics_and_append_mode(weather, tmp_path, monkeypatch):
    from meteorology.run_settings import KEYS
    monkeypatch.chdir(tmp_path)
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    (tmp_path/'.env').write_text(f'DATA_DIR={weather["output_dir"]}\nSTART_DATE=2009-01-01\n'
        'END_DATE=2009-01-01\nMETRICS=pressure\nREBUILD=False\nVERBOSITY=False\n')
    result = get_weather(study_config='study', cache_dir=weather['cache_dir'])
    assert check_weather(result.catalog_path)['products']['daily']['metrics'] == 2
    get_weather('2009-01-02', '2009-01-02', study_config='study', cache_dir=weather['cache_dir'])
    assert check_weather(result.catalog_path)['products']['daily']['rows'] == 2
    # Explicit metric + rebuild options override both configured values.
    get_weather('2009-01-03', '2009-01-03', ['wind'], rebuild=True,
                study_config='study', cache_dir=weather['cache_dir'])
    report = check_weather(result.catalog_path)
    assert report['products']['daily']['rows'] == 1 and report['products']['hourly']['metrics'] == 4



def test_metric_backfill_queries_entire_retained_range_and_aligns_keys(weather, monkeypatch):
    result = get_weather('2009-01-01', '2009-01-02', ['temperature'], **weather)
    # Change row ordering without changing the product's scientific keys/values.
    parquet = pq.ParquetFile(result.hourly_path)
    tables = [parquet.read_row_group(i) for i in range(2)]
    with pq.ParquetWriter(result.hourly_path, tables[0].schema) as writer:
        for table in tables:
            writer.write_table(table.take(pa.array(np.arange(len(table))[::-1])))
    old_catalog = json.loads(result.catalog_path.read_text())
    old_catalog['products']['hourly'].update(bytes=result.hourly_path.stat().st_size,
                                            sha256=checksum_path(result.hourly_path))
    result.catalog_path.write_text(json.dumps(old_catalog))
    saved = pq.read_table(result.hourly_path).to_pydict()
    seen = []
    original = FakeArchive.cube
    def cube(self, day, parameters):
        seen.append((day.day, parameters))
        cube = original(self, day, parameters)
        assert parameters == ['msl']  # no reacquisition of saved temperature
        cube['values'][0] = np.repeat((100000 + np.arange(25))[:, None], 2, axis=1)
        return cube
    monkeypatch.setattr(FakeArchive, 'cube', cube)
    weather['rebuild'] = False
    get_weather('2009-01-02', '2009-01-02', ['pressure'], **weather)
    assert seen == [(1, ['msl']), (2, ['msl'])]
    report = check_weather(result.catalog_path)
    assert report['products']['daily']['metrics'] == 5
    table = pq.read_table(result.hourly_path)
    for name, values in saved.items():
        assert table[name].to_pylist() == values
    frame = table.to_pandas()
    np.testing.assert_allclose(frame.MEAN_SEA_LEVEL_PRESSURE_HPA, 1000 + frame.DATETIME.dt.hour/100)
    epochs = json.loads(result.catalog_path.read_text())['source_epochs']
    assert len(epochs) == 2
    assert epochs[0]['metrics']['hourly'] == ['TEMPERATURE_2M_C']
    assert epochs[1]['metrics']['hourly'] == ['MEAN_SEA_LEVEL_PRESSURE_HPA']
    assert epochs[1]['start_date'] == '2009-01-01' and epochs[1]['end_date'] == '2009-01-02'


def test_metric_expansion_combined_with_missing_dates(weather, monkeypatch):
    result = get_weather('2009-01-02', '2009-01-02', ['temperature'], **weather)
    seen = []
    original = FakeArchive.cube
    def cube(self, day, parameters):
        seen.append((day.day, parameters))
        return original(self, day, parameters)
    monkeypatch.setattr(FakeArchive, 'cube', cube)
    weather['rebuild'] = False
    get_weather('2009-01-01', '2009-01-03', ['wind'], **weather)
    assert seen == [(1, ['t2m', 'u10', 'v10']), (2, ['u10', 'v10']), (3, ['t2m', 'u10', 'v10'])]
    report = check_weather(result.catalog_path, study_config='study')
    assert report['products']['daily']['rows'] == 3 and report['products']['daily']['metrics'] == 8
    assert pq.read_table(result.daily_path)['TEMPERATURE_2M_C_MEAN'].to_pylist() == [10., 10., 10.]


def test_repeated_metric_expansion_and_subset_noop(weather, monkeypatch):
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    weather['rebuild'] = False
    get_weather('2009-01-01', '2009-01-01', ['pressure'], **weather)
    get_weather('2009-01-01', '2009-01-01', ['wind'], **weather)
    epochs = json.loads(result.catalog_path.read_text())['source_epochs']
    scopes = [set(e['metrics']['hourly']) for e in epochs]
    assert len(scopes) == 3 and not scopes[0] & scopes[1] and not scopes[1] & scopes[2]
    assert check_weather(result.catalog_path)['products']['hourly']['metrics'] == 6
    saved = {p: p.read_bytes() for p in (result.catalog_path, result.daily_path, result.hourly_path)}
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('source opened'))
    get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    assert all(p.read_bytes() == value for p, value in saved.items())


def test_metric_backfill_failure_keeps_saved_product(weather, monkeypatch):
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    saved = {p: p.read_bytes() for p in (result.catalog_path, result.daily_path, result.hourly_path)}
    weather['rebuild'] = False
    monkeypatch.setattr(FakeArchive, 'fail', True)
    with pytest.raises(RuntimeError, match='Simulated'):
        get_weather('2009-01-01', '2009-01-01', ['wind'], **weather)
    assert all(p.read_bytes() == value for p, value in saved.items())


def test_new_precipitation_metric_requires_boundary_opt_in(weather, monkeypatch):
    class Frontier(FakeArchive):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.times = self.times[:24]
        def cube(self, day, parameters, allow_missing_boundary=False):
            cube = super().cube(day, parameters)
            cube['values'][:, -1, :] = np.nan
            return cube
    monkeypatch.setattr(regional, 'Archive', Frontier)
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    weather['rebuild'] = False
    with pytest.raises(ValueError, match='coverage'):
        get_weather('2009-01-01', '2009-01-01', ['precipitation'], **weather)
    get_weather('2009-01-01', '2009-01-01', ['precipitation'], allow_incomplete_final_day=True, **weather)
    report = check_weather(result.catalog_path)
    assert report['products']['daily']['null_counts']['PRECIPITATION_MM'] == 1
    assert report['products']['daily']['null_counts']['TEMPERATURE_2M_C_MEAN'] == 0
    catalog = json.loads(result.catalog_path.read_text())
    assert catalog['incomplete_final_day'] and len(catalog['partial_metric_coverage']) == 1



def test_verbose_run_reports_safe_effective_settings_plan_and_timings(weather, monkeypatch, capsys):
    monkeypatch.setenv('CDSAPI_KEY', 'private-token-must-not-appear')
    weather['verbosity'] = True
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    output = capsys.readouterr()
    assert output.out == ''
    for key in ('DATA_DIR', 'START_DATE', 'END_DATE', 'VERBOSITY', 'REBUILD', 'METRICS'):
        assert key in output.err
    for message in ('Preflight passed', 'Mode: REBUILD', 'Missing dates: 1 UTC day',
                    'Query plan: UTC 2009-01-01 through 2009-01-01; native fields t2m',
                    'Source metadata/cache ready', 'query/cache', 'processing/write',
                    'finalization', 'publication', 'API calls 0', 'Weather run completed'):
        assert message in output.err
    assert 'private-token-must-not-appear' not in output.err
    assert all(line.startswith('[meteorology ') for line in output.err.splitlines())
    assert check_weather(result.catalog_path)['valid']


def test_verbose_append_and_noop_report_missing_work(weather, capsys):
    weather['verbosity'] = False
    get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    assert not capsys.readouterr().err
    weather.update(verbosity=True, rebuild=False)
    get_weather('2009-01-02', '2009-01-02', ['pressure'], **weather)
    output = capsys.readouterr()
    assert 'Mode: APPEND' in output.err
    assert 'Saved product validation passed' in output.err
    assert 'Missing dates: 1 UTC day; 2009-01-02 through 2009-01-02' in output.err
    assert 'New metrics to backfill: pressure across 2009-01-01 through 2009-01-01' in output.err
    get_weather('2009-01-01', '2009-01-02', ['temperature', 'pressure'], **weather)
    output = capsys.readouterr()
    assert 'Missing dates: none' in output.err
    assert 'Query 0.00s; processing 0.00s; API calls 0' in output.err
    assert 'Publishing' not in output.err and 'API call #' not in output.err


def test_verbose_cli_keeps_stdout_to_catalog_path(weather, monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['meteorology', 'get', '2009-01-01', '2009-01-01',
        '--variables', 'temperature', '--output-dir', str(weather['output_dir']),
        '--cache-dir', str(weather['cache_dir']), '--verbose', '--rebuild'])
    regional.main()
    output = capsys.readouterr()
    assert output.out.strip() == str(weather['output_dir']/'catalog.json')
    assert len(output.out.splitlines()) == 1
    assert 'Preflight passed' in output.err and 'Weather run completed' in output.err
