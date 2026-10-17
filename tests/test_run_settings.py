"""Run configuration and CLI tests use only isolated local defaults."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from meteorology import regional
from meteorology.run_settings import KEYS, VARIABLES, preflight, resolve


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


def test_file_environment_and_explicit_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv('CDSAPI_KEY', raising=False)
    folder = tmp_path / 'config'
    folder.mkdir()
    file = folder / '.env'
    file.write_text('# defaults\nDATA_DIR="weather files" # relative to file\n'
                    'START_DATE = 2009-01-01\nEND_DATE=2009-01-02\nVERBOSITY=True\n'
                    'CDSAPI_KEY=ignored\nMETRICS=temperature\nREBUILD=False\n')
    assert resolve(env_file=file) == ('2009-01-01', '2009-01-02', folder/'weather files', True, ['temperature'], False)
    monkeypatch.setenv('DATA_DIR', 'process-output')
    monkeypatch.setenv('START_DATE', '2009-01-02')
    monkeypatch.setenv('VERBOSITY', 'false')
    assert resolve(env_file=file) == ('2009-01-02', '2009-01-02', tmp_path/'process-output', False, ['temperature'], False)
    assert resolve('2009-01-01', '2009-01-03', 'explicit', True, file) == (
        '2009-01-01', '2009-01-03', tmp_path/'explicit', True, ['temperature'], False)
    assert 'CDSAPI_KEY' not in __import__('os').environ


@pytest.mark.parametrize('text,match', [
    ('START_DATE=bad', 'START_DATE'),
    ('START_DATE=20090101', 'START_DATE'),
    ('START_DATE=2009-01-03\nEND_DATE=2009-01-02', 'on or before'),
    ('VERBOSITY=maybe', 'VERBOSITY'),
    ('DATA_DIR=""', 'DATA_DIR'),
    ('VERBOSITY', 'line'),
    ('DATA_DIR="unterminated', 'line'),
])
def test_invalid_defaults_rejected_before_source(text, match, monkeypatch):
    Path('.env').write_text('START_DATE=2009-01-01\nEND_DATE=2009-01-02\n'+text+'\n')
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('source opened'))
    with pytest.raises(ValueError, match=match):
        regional.get_weather()


def test_missing_file_dates_and_default_output(tmp_path):
    with pytest.raises(ValueError, match='START_DATE'):
        resolve()
    with pytest.raises(ValueError, match='env_file'):
        resolve(env_file=tmp_path/'absent')
    assert resolve('2009-01-01', '2009-01-02') == (
        '2009-01-01', '2009-01-02', tmp_path/'data/meteorology', False, list(VARIABLES), False)


@pytest.mark.parametrize('args,verbose', [([], None), (['--verbose'], True), (['--no-verbose'], False)])
def test_cli_optional_dates_and_verbosity(args, verbose, monkeypatch, capsys, complete_env):
    seen = {}
    def get(**options):
        seen.update(options)
        return SimpleNamespace(catalog_path=Path('result/catalog.json'))
    monkeypatch.setattr(regional, 'get_weather', get)
    monkeypatch.setattr('sys.argv', ['meteorology', 'get', *args])
    regional.main()
    assert seen['start_date'] is None and seen['end_date'] is None
    assert seen['verbosity'] is verbose
    assert capsys.readouterr().out.strip() == 'result/catalog.json'



def test_metrics_and_rebuild_precedence(monkeypatch):
    Path('.env').write_text('START_DATE=2009-01-01\nEND_DATE=2009-01-02\n'
                            'METRICS=temperature,wind # available families\nREBUILD=True\nDATA_DIR=out\nVERBOSITY=False\n')
    assert resolve()[4:] == (['temperature', 'wind'], True)
    monkeypatch.setenv('METRICS', 'pressure')
    monkeypatch.setenv('REBUILD', 'false')
    assert resolve()[4:] == (['pressure'], False)
    assert resolve(variable_list=['dewpoint'], rebuild=True)[4:] == (['dewpoint'], True)
    monkeypatch.setenv('REBUILD', 'maybe')
    with pytest.raises(ValueError, match='REBUILD'):
        resolve()


@pytest.mark.parametrize('args,replace', [([], None), (['--rebuild'], True), (['--no-rebuild'], False)])
def test_cli_rebuild_and_env_metrics(args, replace, monkeypatch, complete_env):
    seen = {}
    def get(**options):
        seen.update(options)
        return SimpleNamespace(catalog_path=Path('result/catalog.json'))
    monkeypatch.setattr(regional, 'get_weather', get)
    monkeypatch.setattr('sys.argv', ['meteorology', 'get', *args])
    regional.main()
    assert seen['rebuild'] is replace
    assert seen['variable_list'] is None  # config supplies metrics instead of parser defaults


@pytest.fixture
def complete_env():
    path = Path('.env')
    path.write_text('DATA_DIR=weather\nSTART_DATE=2009-01-01\nEND_DATE=2009-01-02\n'
                    'VERBOSITY=False\nMETRICS=temperature,wind\nREBUILD=False\n')
    return path


@pytest.mark.parametrize('key', sorted(KEYS))
def test_preflight_requires_each_setting(complete_env, key):
    lines = complete_env.read_text().splitlines()
    complete_env.write_text('\n'.join(line for line in lines if not line.startswith(key+'=')))
    with pytest.raises(ValueError, match=key + ' must be set'):
        preflight()


def test_preflight_reports_all_errors_before_pipeline(complete_env, monkeypatch):
    complete_env.write_text('DATA_DIR=blocked/child\nSTART_DATE=bad\nEND_DATE=bad\n'
                            'METRICS=wind,wind\nVERBOSITY=maybe\nREBUILD=maybe\n')
    Path('blocked').write_text('a file')
    monkeypatch.setattr('meteorology.marine_reporting.load_reporting_membership',
                        lambda *a: pytest.fail('study opened'))
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('source opened'))
    with pytest.raises(ValueError) as error:
        regional.get_weather()
    assert all(key in str(error.value) for key in KEYS)
    assert not Path('blocked/child').exists()
    assert not list(Path.cwd().glob('.meteorology-*'))


@pytest.mark.parametrize('metrics', ['', 'wind,wind', 'snowfall', 'wind,', 'Wind'])
def test_preflight_rejects_invalid_metrics(complete_env, monkeypatch, metrics):
    monkeypatch.setenv('METRICS', metrics)
    with pytest.raises(ValueError, match='METRICS'):
        preflight()


def test_preflight_checks_parent_access_without_creating_output(complete_env, monkeypatch):
    monkeypatch.setattr('meteorology.run_settings.os.access', lambda *a: False)
    with pytest.raises(ValueError, match='writable'):
        preflight()
    assert not Path('weather').exists()


def test_preflight_duplicate_and_unreadable_file(tmp_path):
    file = tmp_path/'.env'
    file.write_text('VERBOSITY=True\nVERBOSITY=False\n')
    with pytest.raises(ValueError, match='VERBOSITY is repeated'):
        preflight()
    file.write_bytes(b'\xff')
    with pytest.raises(ValueError, match='UTF-8'):
        preflight()
    with pytest.raises(ValueError, match='regular'):
        preflight(env_file=tmp_path)


def test_preflight_cli_validates_without_source_or_output(complete_env, monkeypatch, capsys):
    import json
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('source opened'))
    monkeypatch.setattr('sys.argv', ['meteorology', 'preflight'])
    regional.main()
    result = json.loads(capsys.readouterr().out)
    assert result['METRICS'] == ['temperature', 'wind'] and result['REBUILD'] is False
    assert not Path('weather').exists()


def test_cli_get_requires_complete_settings_without_file(monkeypatch):
    monkeypatch.setattr(regional, 'get_weather', lambda **k: pytest.fail('pipeline started'))
    monkeypatch.setattr('sys.argv', ['meteorology', 'get', '2009-01-01', '2009-01-02'])
    with pytest.raises(ValueError) as error:
        regional.main()
    assert all(key in str(error.value) for key in ['DATA_DIR', 'METRICS', 'REBUILD', 'VERBOSITY'])


def test_preflight_missing_file_and_explicit_overrides(complete_env):
    complete_env.unlink()
    with pytest.raises(ValueError) as error:
        preflight()
    assert all(key in str(error.value) for key in KEYS)
    result = preflight('2009-01-01', '2009-01-02', 'out', False,
                       variable_list=['temperature'], rebuild=False)
    assert result[4:] == (['temperature'], False)
    assert not Path('out').exists()
