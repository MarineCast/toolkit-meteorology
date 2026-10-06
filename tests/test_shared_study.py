from pathlib import Path
from copy import deepcopy
import hashlib
import json

import pytest

from meteorology.cli import initialize_workspace, main
from meteorology.config import load_meteorological_config
from meteorology.study import load_study_config, planning_report, validate_study_identity
from meteorology.artifacts import manifest_payload
from meteorology.hourly_weather.product import _paths

FIXTURE = Path(__file__).parent / 'fixtures' / 'study.proposed.v1.json'
CONFIG_HASH = 'b1f811ff8b3c47bc571805a5845fc55a410ccd6bd08dfc254899b387903ed7f5'
GEOMETRY_HASH = '6d79e4dfd29a4ada66625e20fdcd01e7bfe6076bf6ebb4c449581eb3a0cdfb68'


def test_portable_explicit_path_hashes_and_planning_gate(tmp_path, monkeypatch):
    config_dir = tmp_path / 'portable' / 'config'
    config_dir.mkdir(parents=True)
    selected = config_dir / 'study.json'
    selected.write_bytes(FIXTURE.read_bytes())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('MARINECAST_STUDY_CONFIG', str(tmp_path / 'wrong.json'))
    identity = load_study_config(selected, planning=True)
    assert identity['config_sha256'] == CONFIG_HASH
    assert identity['geometry_sha256'] == GEOMETRY_HASH
    assert Path(identity['resolved_data_root']) == tmp_path / 'portable' / 'Data'
    assert identity['requested_time']['end_exclusive'] == '2027-01-01'
    assert not (tmp_path / 'portable' / 'Data').exists()
    with pytest.raises(ValueError, match='proposed'):
        load_study_config(selected)
    with pytest.raises(FileNotFoundError):
        load_study_config(tmp_path / 'missing.json', planning=True)
    monkeypatch.setenv('MARINECAST_STUDY_CONFIG', str(selected))
    assert planning_report()['study_identity']['config_sha256'] == CONFIG_HASH


@pytest.mark.parametrize('mutation,match', [
    (lambda c: c.update(schema_version=2), 'constant'),
    (lambda c: c['domain']['bbox_wgs84'].__setitem__(0, -130), 'geometry_sha256'),
    (lambda c: c['time'].update(start='2027-01-01'), 'interval'),
    (lambda c: c['storage'].update(data_root='/tmp/nonportable'), 'relative'),
    (lambda c: c['products']['meteorology'].update(h3_resolution=7), 'constant'),
])
def test_invalid_selected_study_fails(tmp_path, mutation, match):
    config = json.loads(FIXTURE.read_text())
    mutation(config)
    p = tmp_path / 'study.json'
    p.write_text(json.dumps(config))
    with pytest.raises(ValueError, match=match):
        load_study_config(p, planning=True)


def test_duplicate_nonfinite_and_missing_explicit_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('MARINECAST_STUDY_CONFIG', str(FIXTURE))
    p = tmp_path / 'invalid.json'
    for content, match in [(' {"schema_version":1,"schema_version":1}', 'duplicate'),
                           (' {"schema_version":NaN}', 'NaN')]:
        p.write_text(content)
        with pytest.raises(ValueError, match=match):
            load_study_config(p, planning=True)
    with pytest.raises(FileNotFoundError):
        load_study_config(tmp_path / 'absent.json', planning=True)


def test_standalone_and_shared_requested_native_config_mapping(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    initialize_workspace(workspace)
    monkeypatch.setenv('METEOROLOGY_WORKSPACE', str(workspace))
    monkeypatch.delenv('MARINECAST_STUDY_CONFIG', raising=False)
    native = load_meteorological_config()
    assert native.study_identity is None
    assert native.surface_weather.timezone == 'America/Los_Angeles'
    selected = tmp_path / 'study.json'
    selected.write_bytes(FIXTURE.read_bytes())
    with pytest.raises(ValueError, match='proposed'):
        load_meteorological_config(study_config=selected)
    planned = load_meteorological_config(study_config=selected, planning=True)
    assert planned.bbox == dict(min_lon=-129.7, min_lat=45.9, max_lon=-121.5, max_lat=51.5)
    assert planned.surface_weather.start_date == '2009-01-01'
    assert planned.surface_weather.end_date == '2026-12-31'
    assert planned.surface_weather.timezone == planned.lunar.timezone == planned.daylight.timezone == 'UTC'
    data = (tmp_path / '../Data').resolve() / 'meteorology'
    assert planned.surface_weather.raw_dir == data / 'raw/surface_weather/hrrr'
    assert _paths(planned)[0] == data / 'raw/hourly_weather/hrrr'
    assert planned.surface_weather.bbox_padding_degrees == .2
    assert planned.study_identity['producer_buffer']['native_grid_policy'] == 'include_complete_sampling_and_interpolation_stencils'
    assert not data.exists()
    # Changing approval status alone cannot turn pending membership into production support.
    config = json.loads(selected.read_text())
    config['domain']['status'] = 'approved'
    selected.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='registry remains pending'):
        load_meteorological_config(study_config=selected)


def test_cli_explicit_preflight_restores_environment_without_writes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('MARINECAST_STUDY_CONFIG', str(tmp_path / 'bad.json'))
    assert main(['--study-config', str(FIXTURE), 'study-preflight']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['study_identity']['config_sha256'] == CONFIG_HASH
    assert report['execution'] == 'planning_only_no_outputs_or_provider_requests'
    assert __import__('os').environ['MARINECAST_STUDY_CONFIG'] == str(tmp_path / 'bad.json')
    with pytest.raises(SystemExit) as failure:
        main(['--study-config', str(tmp_path / 'missing.json'), 'study-preflight'])
    assert failure.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_manifest_identity_and_scientific_buffer_changes_are_pinned():
    identity = load_study_config(FIXTURE, planning=True)
    kwargs = dict(product='unregistered_fixture', run_id='test', config_path=FIXTURE,
                  resolved_config={'actual_source_start':'2024-01-02'},
                  artifacts=[{'checksum':'example'}], study_identity=identity)
    manifest = manifest_payload(**kwargs)
    assert manifest['resolved_config']['shared_study']['config_sha256'] == CONFIG_HASH
    assert manifest['resolved_config']['actual_source_start'] == '2024-01-02'
    # Embedded provenance is independent of the originating host path after publication.
    validate_study_identity(manifest['resolved_config']['shared_study'])
    bad = deepcopy(identity)
    bad['producer_buffer']['legacy_bbox_padding_degrees'] = .3
    with pytest.raises(ValueError, match='metadata differs'):
        manifest_payload(**{**kwargs, 'study_identity':bad})
    changed = deepcopy(identity)
    changed['study_config']['producer_buffers']['meteorology']['legacy_bbox_padding_degrees'] = .3
    changed['producer_buffer']['legacy_bbox_padding_degrees'] = .3
    changed['config_sha256'] = hashlib.sha256(json.dumps(changed['study_config'], sort_keys=True,
           separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    updated = manifest_payload(**{**kwargs, 'study_identity':changed})
    assert updated['release_id'] != manifest['release_id']
    assert updated['resolved_config']['shared_study']['geometry_sha256'] == GEOMETRY_HASH
