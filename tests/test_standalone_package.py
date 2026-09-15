from pathlib import Path
import hashlib
import importlib
import os
import pkgutil
import subprocess
import sys

import pytest

from meteorology import cli
from meteorology.artifacts import code_state
from meteorology.config import load_meteorological_config
from meteorology.core.config.paths import project_root


def test_initialize_external_workspace_preserves_edits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(['init']) == 0
    config = load_meteorological_config()
    assert config.surface_weather.raw_dir.is_relative_to(tmp_path)
    assert config.support_resolutions == (4, 5, 6)
    common = tmp_path / 'config/common.yaml'
    common.write_text(common.read_text() + '\n# user customization\n')
    assert cli.initialize_workspace(tmp_path) == []
    assert common.read_text().endswith('# user customization\n')


def test_workspace_override_restored(tmp_path, monkeypatch):
    monkeypatch.setenv('METEOROLOGY_WORKSPACE', str(tmp_path / 'original'))
    cli.main(['--workspace', str(tmp_path / 'override'), 'init'])
    assert project_root() == tmp_path / 'original'
    assert (tmp_path / 'override/config/data/environment_meteorological.yaml').exists()


def test_source_identity_independent_of_data_workspace(tmp_path, monkeypatch):
    before = code_state()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('METEOROLOGY_WORKSPACE', str(tmp_path))
    assert code_state() == before
    assert before['source_hash'] != hashlib.sha256().hexdigest()


def test_packaged_defaults_match_checkout():
    root = Path(__file__).resolve().parents[1]
    for source in (root / 'config').rglob('*.yaml'):
        packaged = root / 'src/meteorology/resources' / source.relative_to(root)
        assert packaged.read_bytes() == source.read_bytes()


def test_dataset_dependencies_resolve():
    from meteorology.core.data import DATASETS
    assert len(list(DATASETS)) == 10
    for spec in DATASETS:
        for dependency in spec.dependencies:
            DATASETS.get(dependency)


@pytest.mark.parametrize('args', [
    ['build', 'spatial-support'], ['build', 'surface-weather'], ['build', 'daylight'],
    ['build', 'lunar'], ['download', 'surface-weather'], ['inspect', 'spatial-support'],
    ['inspect', 'surface-weather'], ['inspect', 'daylight'], ['inspect', 'lunar'],
    ['catalog'], ['feature-policy'], ['verify'], ['benchmark'], ['migrate-legacy'],
])
def test_cli_routes_help(args, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([*args, '--help'])
    assert exc.value.code == 0
    assert 'usage:' in capsys.readouterr().out


def test_imports_do_not_require_orcacast(tmp_path):
    script = '''
import importlib, pkgutil, sys
class RejectApplication:
    def find_spec(self, fullname, *args):
        if fullname == 'orcacast' or fullname.startswith('orcacast.'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, RejectApplication())
import meteorology
for module in pkgutil.walk_packages(meteorology.__path__, 'meteorology.'):
    if not module.name.endswith('.__main__'):
        importlib.import_module(module.name)
'''
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    subprocess.run([sys.executable, '-c', script], cwd=tmp_path, env=env, check=True)
