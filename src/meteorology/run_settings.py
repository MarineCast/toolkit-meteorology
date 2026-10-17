"""Side-effect-free run preflight: arguments > environment > local .env."""
from datetime import date
import os
from pathlib import Path
import shlex

VARIABLES = ('temperature', 'dewpoint', 'relative_humidity', 'wind',
             'cloud_cover', 'pressure', 'precipitation')
KEYS = {'DATA_DIR', 'START_DATE', 'END_DATE', 'VERBOSITY', 'METRICS', 'REBUILD'}


def preflight(start_date=None, end_date=None, output_dir=None, verbosity=None, env_file=None,
              *, variable_list=None, rebuild=None, require_all=True):
    """Validate all effective settings together without writes, locks or network.

    File-backed runs require all six settings, with explicit/environment overrides
    allowed. Direct API calls without a file can retain defaults via resolve().
    Errors identify keys/line numbers, never raw values or unrelated credentials.
    """
    errors, values = [], {}
    path = Path(env_file).expanduser().resolve() if env_file is not None else Path.cwd() / '.env'
    try:
        exists = path.exists()
        if exists and not path.is_file():
            errors.append('env_file must be a regular .env file.')
        elif exists:
            seen = set()
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('export '):
                    line = line[7:].lstrip()
                key, separator, raw = line.partition('=')
                key = key.strip()
                if key not in KEYS:
                    continue
                if key in seen:
                    errors.append(f'{key} is repeated on .env line {number}.')
                seen.add(key)
                try:
                    tokens = shlex.split(raw, comments=True)
                    if not separator or len(tokens) != 1:
                        raise ValueError
                    values[key] = tokens[0]
                except ValueError:
                    errors.append(f'{key} has invalid syntax on .env line {number}.')
        elif env_file is not None:
            errors.append('Explicit env_file does not exist.')
    except (OSError, UnicodeError):
        exists = True
        errors.append('env_file cannot be read as UTF-8.')
    file_output = 'DATA_DIR' in values and 'DATA_DIR' not in os.environ and output_dir is None
    values.update({key: os.environ[key] for key in KEYS if key in os.environ})
    for key, value in [('START_DATE', start_date), ('END_DATE', end_date),
                       ('DATA_DIR', output_dir), ('VERBOSITY', verbosity),
                       ('METRICS', variable_list), ('REBUILD', rebuild)]:
        if value is not None:
            values[key] = value
    if not (require_all or exists or env_file is not None):
        for key, default in [('DATA_DIR', 'data/meteorology'), ('VERBOSITY', False),
                             ('METRICS', list(VARIABLES)), ('REBUILD', False)]:
            values.setdefault(key, default)
    missing = {key for key in KEYS if key not in values or values[key] is None
               or (isinstance(values[key], str) and not values[key].strip())}
    errors.extend(f'{key} must be set.' for key in sorted(missing))
    dates = {}
    for key in ('START_DATE', 'END_DATE'):
        if key in missing:
            continue
        try:
            dates[key] = date.fromisoformat(str(values[key]))
            if dates[key].isoformat() != str(values[key]):
                raise ValueError
        except (TypeError, ValueError):
            dates.pop(key, None)
            errors.append(f'{key} must be a valid YYYY-MM-DD date.')
    if len(dates) == 2 and dates['START_DATE'] > dates['END_DATE']:
        errors.append('START_DATE must be on or before END_DATE (start_date <= end_date).')
    for key in ('VERBOSITY', 'REBUILD'):
        if key in missing:
            continue
        value = values[key]
        if isinstance(value, str) and value.lower() in {'true', 'false'}:
            value = value.lower() == 'true'
        if type(value) is not bool:
            errors.append(f'{key} must be True or False.')
        values[key] = value
    if 'METRICS' not in missing:
        metrics = values['METRICS']
        # Environment/file values are CSV; an explicit API string remains invalid.
        if variable_list is None and isinstance(metrics, str):
            metrics = [part.strip() for part in metrics.split(',')]
        try:
            metrics = list(metrics) if not isinstance(metrics, str) else []
            if not metrics or any(not isinstance(v, str) or v not in VARIABLES for v in metrics):
                raise ValueError
            if len(set(metrics)) != len(metrics):
                raise ValueError
            values['METRICS'] = metrics
        except (TypeError, ValueError):
            errors.append('METRICS must be nonempty, unique allowed names: ' + ', '.join(VARIABLES))
    target = None
    if 'DATA_DIR' not in missing:
        try:
            raw = os.fspath(values['DATA_DIR'])
            if not isinstance(raw, str) or not raw.strip() or '\x00' in raw:
                raise ValueError
            target = Path(raw).expanduser()
            if file_output and not target.is_absolute():
                target = path.parent / target
            target = target.resolve()
            ancestor = target
            while not ancestor.exists():
                ancestor = ancestor.parent
            if not ancestor.is_dir():
                errors.append('DATA_DIR or its existing ancestor must be a directory.')
            elif not os.access(ancestor, os.W_OK | os.X_OK):
                errors.append('DATA_DIR or its existing parent must be writable and searchable.')
        except (TypeError, ValueError, OSError, RuntimeError):
            errors.append('DATA_DIR must be a usable directory path.')
    if errors:
        raise ValueError('Weather preflight failed:\n- ' + '\n- '.join(errors))
    return (dates['START_DATE'].isoformat(), dates['END_DATE'].isoformat(), target,
            values['VERBOSITY'], values['METRICS'], values['REBUILD'])


def resolve(*args, **options):
    """Run the same preflight, retaining defaults for file-free Python API calls."""
    return preflight(*args, require_all=False, **options)
