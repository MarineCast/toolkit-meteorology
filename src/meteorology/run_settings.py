"""Small run defaults: explicit arguments > process environment > local .env."""
from datetime import date
import os
from pathlib import Path
import shlex

KEYS = {'DATA_DIR', 'START_DATE', 'END_DATE', 'VERBOSITY', 'METRICS', 'REBUILD'}


def resolve(start_date=None, end_date=None, output_dir=None, verbosity=None, env_file=None, *, variable_list=None, rebuild=None):
    """Read only known keys; never execute values or change the process environment."""
    path = Path(env_file).expanduser().resolve() if env_file is not None else Path.cwd() / '.env'
    values = {}
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:].lstrip()
            key, separator, raw = line.partition('=')
            if key.strip() not in KEYS:
                continue
            try:
                tokens = shlex.split(raw, comments=True)
                if not separator or len(tokens) != 1:
                    raise ValueError
            except ValueError:
                raise ValueError(f'Invalid run setting on .env line {number}.') from None
            values[key.strip()] = tokens[0]
    elif env_file is not None:
        raise ValueError('Explicit env_file does not exist.')
    file_output = 'DATA_DIR' in values and 'DATA_DIR' not in os.environ and output_dir is None
    values.update({key: os.environ[key] for key in KEYS if key in os.environ})
    first = start_date if start_date is not None else values.get('START_DATE')
    last = end_date if end_date is not None else values.get('END_DATE')
    for name, value in [('START_DATE', first), ('END_DATE', last)]:
        try:
            if date.fromisoformat(str(value)).isoformat() != str(value):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f'Supply {name} as YYYY-MM-DD via argument, environment or .env.') from None
    if str(first) > str(last):
        raise ValueError('start_date must be on or before end_date.')
    target = output_dir if output_dir is not None else values.get('DATA_DIR', 'data/meteorology')
    if not str(target).strip():
        raise ValueError('DATA_DIR must not be empty.')
    target = Path(target).expanduser()
    if file_output and not target.is_absolute():
        target = path.parent / target
    verbose = verbosity if verbosity is not None else values.get('VERBOSITY', 'False')
    if isinstance(verbose, str) and verbose.lower() in {'true', 'false'}:
        verbose = verbose.lower() == 'true'
    if type(verbose) is not bool:
        raise ValueError('VERBOSITY must be True or False.')
    replace = rebuild if rebuild is not None else values.get('REBUILD', 'False')
    if isinstance(replace, str) and replace.lower() in {'true', 'false'}:
        replace = replace.lower() == 'true'
    if type(replace) is not bool:
        raise ValueError('REBUILD must be True or False.')
    metrics = variable_list
    if metrics is None and 'METRICS' in values:
        metrics = [value.strip() for value in values['METRICS'].split(',')]
    return first, last, target.resolve(), verbose, metrics, replace
