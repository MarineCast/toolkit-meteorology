"""Local configuration without environment mutation or secret-bearing diagnostics."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
import os
from pathlib import Path
import shlex
import sys

VARIABLES = ('temperature', 'dewpoint', 'relative_humidity', 'wind',
             'cloud_cover', 'pressure', 'precipitation')
BOUNDS = ('MIN_LON', 'MIN_LAT', 'MAX_LON', 'MAX_LAT')
KEYS = {'DATA_DIR', 'START_DATE', 'END_DATE', 'VERBOSITY', 'METRICS', 'REBUILD',
        'MARINECAST_STUDY_CONFIG', *BOUNDS}
TOKEN_KEYS = {'CDSAPI_KEY', 'CDSAPI_URL', 'COPERNICUS_API_KEY'}


def read_env(env_file=None, *, keys=KEYS):
    """Read selected assignments; errors report key/line only, never values."""
    path = Path(env_file).expanduser().resolve() if env_file is not None else Path.cwd()/'.env'
    values, errors = {}, []
    try:
        if not path.exists():
            if env_file is not None:
                raise ValueError('Explicit env_file does not exist.')
            return path, values
        if not path.is_file():
            raise ValueError('env_file must be a regular .env file.')
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:].lstrip()
            key, separator, raw = line.partition('=')
            key = key.strip()
            if key not in keys:
                continue
            if key in values:
                errors.append(f'{key} is repeated on .env line {number}.')
            try:
                tokens = shlex.split(raw, comments=True)
                if not separator or len(tokens) > 1:
                    raise ValueError
                values[key] = tokens[0] if tokens else ''
            except ValueError:
                errors.append(f'{key} has invalid syntax on .env line {number}.')
    except (OSError, UnicodeError):
        raise ValueError('env_file cannot be read as UTF-8; details redacted.') from None
    if errors:
        raise ValueError('Configuration failed:\n- ' + '\n- '.join(errors))
    return path, values


@dataclass(frozen=True)
class RunSettings:
    start_date: str | None
    end_date: str | None
    output_dir: Path | None
    verbosity: bool
    metrics: list[str]
    rebuild: bool
    study_config: Path | None
    bbox: list[float] | None
    env_file: Path | None

    def weather_options(self):
        return (self.start_date, self.end_date, self.output_dir, self.verbosity,
                self.metrics, self.rebuild)


def resolve_run(start_date=None, end_date=None, output_dir=None, verbosity=None, env_file=None,
                *, variable_list=None, rebuild=None, study_config=None, bbox=None,
                require_dates=True, product=None):
    """Arguments > process environment > selected .env > optional defaults.

    DATA_DIR is the Meteorology root; companions append their own product name.
    Explicit output_dir always names the exact destination. Bounds are optional
    for compatibility, but if present must specify all four corners. Missing
    bounds select the approved study envelope. No credentials enter this record.
    """
    path, values = read_env(env_file)
    file_keys = values.keys() - os.environ.keys()
    values.update({key: os.environ[key] for key in KEYS if key in os.environ})
    explicit = dict(START_DATE=start_date, END_DATE=end_date, DATA_DIR=output_dir,
                    VERBOSITY=verbosity, METRICS=variable_list, REBUILD=rebuild,
                    MARINECAST_STUDY_CONFIG=study_config)
    values.update({key: value for key, value in explicit.items() if value is not None})
    values.setdefault('VERBOSITY', False)
    values.setdefault('METRICS', list(VARIABLES))
    values.setdefault('REBUILD', False)
    if product is None:
        values.setdefault('DATA_DIR', 'data/meteorology')
    errors, dates = [], {}
    for key in ('START_DATE', 'END_DATE'):
        value = values.get(key)
        if value is None and not require_dates:
            dates[key] = None
            continue
        try:
            if value is None or value == '':
                errors.append(f'{key} must be set.')
                continue
            parsed = date.fromisoformat(str(value))
            if parsed.isoformat() != str(value):
                raise ValueError
            dates[key] = str(value)
        except (TypeError, ValueError):
            errors.append(f'{key} must be a valid YYYY-MM-DD date.')
    if all(dates.get(k) for k in ('START_DATE', 'END_DATE')) and dates['START_DATE'] > dates['END_DATE']:
        errors.append('START_DATE must be on or before END_DATE (start_date <= end_date).')
    for key in ('VERBOSITY', 'REBUILD'):
        value = values[key]
        if isinstance(value, str) and value.lower() in {'true', 'false'}:
            value = value.lower() == 'true'
        if type(value) is not bool:
            errors.append(f'{key} must be True or False.')
        values[key] = value
    metrics = values['METRICS']
    if variable_list is None and isinstance(metrics, str):
        metrics = [part.strip() for part in metrics.split(',')]
    try:
        metrics = list(metrics) if not isinstance(metrics, str) else []
        if (not metrics or any(not isinstance(v, str) or v not in VARIABLES for v in metrics)
                or len(set(metrics)) != len(metrics)):
            raise ValueError
    except (TypeError, ValueError):
        errors.append('METRICS must be nonempty, unique allowed names: ' + ', '.join(VARIABLES))
    bounds = None
    if bbox is not None or any(key in values for key in BOUNDS):
        try:
            bounds = [float(v) for v in bbox] if bbox is not None else [float(values[k]) for k in BOUNDS]
            w, s, e, n = bounds
            if not all(math.isfinite(v) for v in bounds) or not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            errors.append('MIN_LAT, MAX_LAT, MIN_LON, MAX_LON must all be finite, ordered WGS84 bounds.')
    paths = {}
    for key in ('DATA_DIR', 'MARINECAST_STUDY_CONFIG'):
        value = values.get(key)
        if value is None:
            paths[key] = None
            continue
        try:
            raw = os.fspath(value)
            if not isinstance(raw, str) or not raw.strip() or '\x00' in raw:
                raise ValueError
            target = Path(raw).expanduser()
            if key in file_keys and explicit[key] is None and not target.is_absolute():
                target = path.parent/target
            if key == 'DATA_DIR':
                if product is not None and output_dir is None:
                    target /= product
                # Inspect the lexical path before resolve() erases symlinks.
                target = target.absolute()
                if any(p.is_symlink() for p in (target, *target.parents)):
                    errors.append('DATA_DIR and its ancestors must not be symlinks.')
                    continue
            target = target.resolve()
            if key == 'DATA_DIR':
                ancestor = target
                while not ancestor.exists():
                    ancestor = ancestor.parent
                if not ancestor.is_dir():
                    errors.append('DATA_DIR or its existing ancestor must be a directory.')
                elif not os.access(ancestor, os.W_OK | os.X_OK):
                    errors.append('DATA_DIR or its existing parent must be writable and searchable.')
            paths[key] = target
        except (TypeError, ValueError, OSError, RuntimeError):
            errors.append(f'{key} must be a usable path.')
    if errors:
        raise ValueError('Meteorology preflight failed:\n- ' + '\n- '.join(errors))
    return RunSettings(dates['START_DATE'], dates['END_DATE'], paths['DATA_DIR'],
                       values['VERBOSITY'], metrics, values['REBUILD'],
                       paths['MARINECAST_STUDY_CONFIG'], bounds, path if path.is_file() else None)


def preflight(*args, **options):
    """Compatibility tuple for the Weather settings gate; no side effects."""
    options.pop('require_all', None)
    return resolve_run(*args, **options).weather_options()


def resolve(*args, **options):
    return preflight(*args, **options)


def log(verbosity, message):
    if verbosity:
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        sys.stderr.write(f'[meteorology {stamp}] {message}\n')
        sys.stderr.flush()
