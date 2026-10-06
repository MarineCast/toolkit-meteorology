#!/usr/bin/env python3
"""Validate MarineCast study v1 JSON; no runtime or sibling-repo dependencies."""
from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def parse_json_bytes(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def read_json(path):
    return parse_json_bytes(Path(path).read_bytes())


def check(value, schema, path='$'):
    """Validate the deliberately small schema vocabulary used by study.schema.json."""
    types = schema.get('type', [])
    types = [types] if isinstance(types, str) else types
    matches = {'object': isinstance(value, dict), 'array': isinstance(value, list),
               'string': isinstance(value, str), 'null': value is None,
               'integer': type(value) is int,
               'number': type(value) in (int, float) and math.isfinite(value)}
    if types and not any(matches[t] for t in types):
        raise ValueError(f'{path}: expected {types}')
    if 'const' in schema and (value != schema['const'] or type(value) != type(schema['const'])):
        raise ValueError(f'{path}: unexpected constant')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path}: invalid enum')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        missing = set(schema.get('required', [])) - value.keys()
        unknown = value.keys() - properties.keys()
        if missing or (unknown and schema.get('additionalProperties') is False):
            raise ValueError(f'{path}: missing={sorted(missing)}, unknown={sorted(unknown)}')
        for key in value.keys() & properties.keys():
            check(value[key], properties[key], f'{path}.{key}')
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', math.inf):
            raise ValueError(f'{path}: invalid array length')
        for index, item in enumerate(value):
            check(item, schema.get('items', {}), f'{path}[{index}]')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0):
            raise ValueError(f'{path}: string too short')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            raise ValueError(f'{path}: invalid pattern')
        if schema.get('format') == 'date':
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError(f'{path}: expected YYYY-MM-DD')
            date.fromisoformat(value)
        if schema.get('format') == 'date-time':
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError(f'{path}: approval timestamp requires timezone')
    if type(value) in (int, float) and value < schema.get('minimum', -math.inf):
        raise ValueError(f'{path}: below minimum')


def geometry_identity(config):
    west, south, east, north = config['domain']['bbox_wgs84']
    if not (-180 <= west < east <= 180 and -90 < south < north < 90):
        raise ValueError('invalid non-antimeridian WGS84 rectangle')
    geometry = {'type': 'Polygon', 'coordinates': [[[west, south], [east, south],
                 [east, north], [west, north], [west, south]]]}
    identity = {'crs': config['domain']['crs'],
                'boundary_semantics': config['domain']['boundary_semantics'], 'geometry': geometry}
    return hashlib.sha256(canonical_bytes(identity)).hexdigest()


def resolve_config_path(explicit_path=None):
    # Explicit path always wins; there is no guessed default or sibling lookup.
    selected = explicit_path if explicit_path is not None else os.environ.get('MARINECAST_STUDY_CONFIG')
    if not selected:
        raise ValueError('supply --study-config PATH or MARINECAST_STUDY_CONFIG')
    return Path(selected).expanduser().resolve()


def _check_selection_policy(config, require_approved):
    # Coastal production gates do not depend on an optional policy being present.
    # Schema requires these fields even for planning; malformed/null policies fail there.
    policy = config['domain']['selection_policy']
    if policy['status'] == 'approved' and not policy['approval']:
        raise ValueError('approved selection policy requires approval provenance')
    if require_approved and (policy['status'] != 'approved' or
            policy['mask_status'] != 'source_relative_validated' or
            config['domain']['geometry_status'] != 'source_relative_validated' or
            config['grid_registry']['status'] != 'validated'):
        raise ValueError('production requires validated coastal mask, geometry and registry')


def _validate(path=None, require_approved=True):
    path = resolve_config_path(path)
    raw = path.read_bytes()
    schema_raw = (Path(__file__).parent / 'resources' / 'study.schema.json').read_bytes()
    config = parse_json_bytes(raw)
    check(config, parse_json_bytes(schema_raw))
    if date.fromisoformat(config['time']['start']) >= date.fromisoformat(config['time']['end_exclusive']):
        raise ValueError('requested date interval must have start < end_exclusive')
    geometry_hash = geometry_identity(config)
    if config['domain']['geometry_sha256'] != geometry_hash:
        raise ValueError('geometry_sha256 mismatch: update revision and identity deliberately')
    if require_approved and config['domain']['status'] != 'approved':
        raise ValueError('domain remains proposed; production run requires approved geometry')
    if config['domain']['status'] == 'approved' and not config['domain'].get('approval'):
        raise ValueError('approved domain requires explicit approval provenance')
    _check_selection_policy(config, require_approved)
    registry = config['grid_registry']
    if registry['status'] == 'validated':
        if not registry['mask_revision'] or not registry['mask_sha256'] or not registry['memberships']:
            raise ValueError('validated registry requires pinned mask and memberships')
    roles = [(m['resolution'], m['role']) for m in registry['memberships']]
    if len(roles) != len(set(roles)):
        raise ValueError('duplicate registry resolution/role')
    root = Path(config['storage']['data_root'])
    if root.is_absolute():
        raise ValueError('data_root must be portable and relative to the config file')
    return config, {'raw_file_sha256': hashlib.sha256(raw).hexdigest(),
                    'schema_sha256': hashlib.sha256(schema_raw).hexdigest(), 'study_id': config['study_id'], 'domain_status': config['domain']['status'],
                    'domain_revision': config['domain']['revision'],
                    'geometry_sha256': geometry_hash,
                    'config_sha256': hashlib.sha256(canonical_bytes(config)).hexdigest(),
                    'resolved_data_root': str((path.parent / root).resolve())}



def load_study_config(explicit_path=None, *, planning=False):
    """Load the exact portable v1 contract; absent selection preserves standalone use.

    Production requires approved geometry and a validated reporting registry. Native
    meteorology companions retain the rectangle; this adapter does not create a
    water-mask membership or certify the mask's scientific validity.
    """
    selected = explicit_path if explicit_path is not None else os.environ.get('MARINECAST_STUDY_CONFIG')
    if selected is None:
        return None
    config, identity = _validate(selected, require_approved=not planning)
    if not planning and config['grid_registry']['status'] != 'validated':
        raise ValueError('shared study reporting registry remains pending; production requires pinned memberships')
    identity.update(requested_time=dict(config['time']),
                    producer_buffer=dict(config['producer_buffers']['meteorology']),
                    native_support_role='rectangle_centroid_atmospheric_companion_not_water_reporting',
                    study_config=config)
    return identity


def planning_report(explicit_path=None):
    identity = load_study_config(explicit_path, planning=True)
    if identity is None:
        raise ValueError('supply --study-config PATH or MARINECAST_STUDY_CONFIG')
    return dict(study_identity=identity, execution='planning_only_no_outputs_or_provider_requests',
                actual_source_coverage='not_inspected',
                reporting_membership='requires validated marine registry; no bbox-center counts certified')


def validate_study_identity(identity):
    """Check embedded canonical identity without opening the original host config."""
    config = identity['study_config']
    check(config, read_json(Path(__file__).parent / 'resources' / 'study.schema.json'))
    if config['domain']['status'] == 'approved' and not config['domain'].get('approval'):
        raise ValueError('approved domain requires explicit approval provenance')
    _check_selection_policy(config, False)
    expected = hashlib.sha256(canonical_bytes(config)).hexdigest()
    if identity['config_sha256'] != expected or identity['geometry_sha256'] != geometry_identity(config):
        raise ValueError('embedded shared study canonical identity mismatch')
    if config['domain']['geometry_sha256'] != identity['geometry_sha256']:
        raise ValueError('embedded shared study geometry identity mismatch')
    if (identity['domain_revision'] != config['domain']['revision']
            or identity['domain_status'] != config['domain']['status']
            or identity['requested_time'] != config['time']
            or identity['producer_buffer'] != config['producer_buffers']['meteorology']):
        raise ValueError('embedded shared study metadata differs from canonical config')
    if date.fromisoformat(config['time']['start']) >= date.fromisoformat(config['time']['end_exclusive']):
        raise ValueError('embedded shared study requested date interval is invalid')
    for name in ('raw_file_sha256', 'schema_sha256'):
        if not re.fullmatch('[0-9a-f]{64}', identity.get(name, '')):
            raise ValueError(f'embedded shared study {name} is invalid')


def reject_study_selection(command):
    """Input-bound utilities cannot apply a selected study; fail before any I/O."""
    identity = load_study_config(planning=True)
    if identity is not None:
        raise ValueError(f'{command} does not support study selection; use pinned input manifests without a selector')
