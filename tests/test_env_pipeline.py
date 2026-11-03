"""Repository .env drives bounded coastal selection and all product run modes."""
import json

import numpy as np
import pyarrow.parquet as pq
import pytest

from meteorology import get_weather, get_solar_cycle, get_solar_radiation
from meteorology import marine_reporting, regional, solar, radiation
from meteorology.run_settings import resolve_run
from .test_coastal import FakeArchive
from .test_radiation import RadiationArchive
from .test_marine_reporting import registry


@pytest.fixture
def configured(tmp_path, monkeypatch):
    member, study, _, _ = registry(tmp_path)
    for module in (marine_reporting, solar, radiation):
        monkeypatch.setattr(module, 'load_reporting_membership', lambda _: member)
    monkeypatch.setattr(regional, 'Archive', FakeArchive)
    monkeypatch.setattr(regional, 'mapping', lambda grid, bbox, cache: (
        np.arange(len(member.cells)) % 2, list(member.cells), np.zeros(len(member.cells)), 'test'))
    monkeypatch.setattr(radiation, 'Archive', RadiationArchive)
    monkeypatch.setattr(radiation, '_spatial', lambda archive, selected, cache: (
        np.arange(len(selected.cells)) % 2, np.zeros(len(selected.cells)), 'test'))
    for key in ('fail', 'missing_rain', 'invalid_rh'):
        monkeypatch.setattr(FakeArchive, key, False)
    for key, value in dict(end='2009-01-13T00:00:00Z', version=1, fail_day=None,
                           invalid_units=False, requests_seen=[]).items():
        monkeypatch.setattr(RadiationArchive, key, value)
    env = tmp_path/'.env'
    def configure(start='2009-01-02', end='2009-01-02', rebuild=False, subset=True):
        env.write_text(f'START_DATE={start}\nEND_DATE={end}\nREBUILD={str(rebuild).lower()}\n'
                       f'DATA_DIR={tmp_path}/output\nMARINECAST_STUDY_CONFIG={study}\n'
                       'CDSAPI_KEY=TEST_TOKEN_DO_NOT_LOG\n'
                       + ('MIN_LAT=48.5\nMAX_LAT=49.5\nMIN_LON=-125.5\nMAX_LON=-124.5\n'
                          if subset else 'MIN_LAT=45.9\nMAX_LAT=51.5\nMIN_LON=-129.7\nMAX_LON=-121.5\n'))
    configure()
    return member, env, configure


@pytest.mark.parametrize('get', [get_weather, get_solar_cycle, get_solar_radiation])
def test_env_subset_append_rebuild_and_region_gate(configured, get):
    member, env, configure = configured
    result = get(env_file=env)
    old = pq.read_table(result.daily_path).to_pandas()
    assert len(old) < len(member.cells)
    assert set(old.H3_INDEX) < set(member.cells)
    catalog = json.loads(result.catalog_path.read_text())
    assert catalog['parent_membership_sha256'] == member.artifact_sha256
    before = {p: p.read_bytes() for p in (result.daily_path, result.hourly_path, result.catalog_path)}
    get(env_file=env)
    assert all(p.read_bytes() == content for p, content in before.items())
    configure(start='2009-01-01', end='2009-01-03')
    get(env_file=env)
    current = pq.read_table(result.daily_path).to_pandas()
    assert len(current) == 3*len(old)
    assert current[current.DATE == old.DATE.iloc[0]].reset_index(drop=True).equals(old)
    # Region changes are never appended to an existing spatial identity.
    configure(start='2009-01-01', end='2009-01-03', subset=False)
    with pytest.raises(ValueError, match='membership|envelope|conventions'):
        get(env_file=env)
    configure(start='2009-01-03', end='2009-01-03', rebuild=True, subset=False)
    get(env_file=env)
    new = pq.read_table(result.daily_path).to_pandas()
    assert len(new) == len(member.cells) and set(new.H3_INDEX) == set(member.cells)
    assert new.DATE.nunique() == 1
    assert 'TEST_TOKEN_DO_NOT_LOG' not in result.catalog_path.read_text()


@pytest.mark.parametrize('get,checker', [(get_weather, regional.check_weather),
                                       (get_solar_cycle, solar.check_solar_cycle),
                                       (get_solar_radiation, radiation.check_solar_radiation)])
def test_selected_product_checker_uses_catalog_region(configured, get, checker):
    _, env, _ = configured
    result = get(env_file=env)
    assert checker(result.catalog_path, study_config='study')['membership_verified']


@pytest.mark.parametrize('bounds', [
    'MIN_LAT=48\n',
    'MIN_LAT=nan\nMAX_LAT=50\nMIN_LON=-126\nMAX_LON=-124\n',
    'MIN_LAT=50\nMAX_LAT=48\nMIN_LON=-126\nMAX_LON=-124\n',
    'MIN_LAT=48\nMAX_LAT=50\nMIN_LON=-124\nMAX_LON=-126\n',
])
def test_invalid_bounds_fail_before_study_or_writes(tmp_path, monkeypatch, bounds):
    (tmp_path/'.env').write_text('START_DATE=2009-01-01\nEND_DATE=2009-01-02\n'+bounds)
    monkeypatch.setattr(marine_reporting, 'load_reporting_membership', lambda _: pytest.fail('study accessed'))
    with pytest.raises(ValueError, match='MIN_LAT'):
        get_weather()
    assert not (tmp_path/'data').exists()


def test_shared_configuration_paths_precedence_and_redaction(tmp_path, monkeypatch):
    folder = tmp_path/'settings'
    folder.mkdir()
    env = folder/'.env'
    env.write_text('START_DATE=2009-01-01\nEND_DATE=2009-01-02\nREBUILD=false\n'
                   'DATA_DIR=products\nMARINECAST_STUDY_CONFIG=study.json\nCDSAPI_KEY=SECRET_SENTINEL\n'
                   'MIN_LAT=48\nMAX_LAT=50\nMIN_LON=-126\nMAX_LON=-124\n')
    settings = resolve_run(env_file=env, product='solar-cycle')
    assert settings.output_dir == folder/'products/solar-cycle'
    assert settings.study_config == folder/'study.json'
    assert 'SECRET_SENTINEL' not in repr(settings)
    monkeypatch.setenv('REBUILD', 'true')
    assert resolve_run(env_file=env).rebuild
    assert not resolve_run(env_file=env, rebuild=False).rebuild
    assert resolve_run(env_file=env, bbox=[-127,47,-123,51]).bbox == [-127,47,-123,51]
    assert 'CDSAPI_KEY' not in __import__('os').environ


def test_fetch_uses_selected_env_without_secret_output(tmp_path, monkeypatch, capsys):
    from meteorology import regional_source
    env = tmp_path/'chosen.env'
    env.write_text('CDSAPI_KEY="TOKEN_SENTINEL"\n')
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b'fixture'
    class Opener:
        def open(self, request, timeout):
            assert request.get_header('Authorization') == 'Bearer TOKEN_SENTINEL'
            return Response()
    monkeypatch.setattr(regional_source, 'build_opener', lambda *args: Opener())
    assert regional_source.fetch('.zmetadata', 100, env_file=env) == b'fixture'
    assert 'TOKEN_SENTINEL' not in str(capsys.readouterr())
    assert 'CDSAPI_KEY' not in __import__('os').environ


@pytest.mark.parametrize('get', [get_weather, get_solar_cycle, get_solar_radiation])
@pytest.mark.parametrize('source', ['file', 'environment', 'argument'])
@pytest.mark.parametrize('kind', ['destination', 'ancestor', 'dangling'])
def test_output_symlinks_fail_before_study_or_writes(tmp_path, monkeypatch, get, source, kind):
    target = tmp_path/'retained'
    target.mkdir()
    sentinel = target/'user-file'
    sentinel.write_bytes(b'preserve')
    link = tmp_path/'linked'
    link.symlink_to(target if kind != 'dangling' else tmp_path/'missing', target_is_directory=True)
    output = link/'new-output' if kind == 'ancestor' else link
    env = tmp_path/'.env'
    env.write_text('START_DATE=2009-01-01\nEND_DATE=2009-01-02\n')
    options = {'env_file': env}
    if source == 'file':
        with env.open('a') as handle:
            handle.write(f'DATA_DIR={output}\n')
    elif source == 'environment':
        monkeypatch.setenv('DATA_DIR', str(output))
    else:
        options['output_dir'] = output
    for module in (marine_reporting, solar, radiation):
        monkeypatch.setattr(module, 'load_reporting_membership',
                            lambda _: pytest.fail('study accessed before rejecting symlink'))
    before = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*')}
    with pytest.raises(ValueError, match='DATA_DIR and its ancestors must not be symlinks'):
        get(**options)
    assert sentinel.read_bytes() == b'preserve'
    assert link.is_symlink()
    assert {str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*')} == before


@pytest.mark.parametrize('product', ['solar-cycle', 'solar-radiation'])
def test_configured_companion_destination_symlink_is_rejected(tmp_path, product):
    root = tmp_path/'output'
    root.mkdir()
    target = tmp_path/'retained'
    target.mkdir()
    (root/product).symlink_to(target, target_is_directory=True)
    env = tmp_path/'.env'
    env.write_text(f'DATA_DIR={root}\n')
    with pytest.raises(ValueError, match='must not be symlinks'):
        resolve_run(env_file=env, product=product, require_dates=False)
