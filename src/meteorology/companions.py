"""Recognize only explicitly supported child products alongside ERA5 weather."""
import json
from pathlib import Path


def is_meteorology_companion(path):
    path = Path(path)
    if path.name == 'solar-cycle':
        from .solar import is_solar_directory
        return is_solar_directory(path)
    if path.name != 'solar-radiation' or path.is_symlink() or not path.is_dir():
        return False
    from .radiation import METHOD, PRODUCT
    expected = {'catalog.json', 'solar-radiation-hourly.parquet', 'solar-radiation-daily.parquet'}
    try:
        if {p.name for p in path.iterdir()} != expected or any(
                p.is_symlink() or not p.is_file() for p in path.iterdir()):
            return False
        catalog = json.loads((path/'catalog.json').read_text())
        return (catalog.get('product') == PRODUCT and catalog.get('method') == METHOD
                and catalog.get('schema_version') == 1)
    except (OSError, ValueError):
        return False
