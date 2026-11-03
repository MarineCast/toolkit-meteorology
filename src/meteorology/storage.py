"""Product-local temporary storage, never inside the source checkout."""
import hashlib
from pathlib import Path


def data_path(path):
    """Reject symlinks and checkout-local data before creating anything."""
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Data paths and their ancestors must not be symlinks.')
    path = path.resolve()
    # Inspect the destination, so an installed wheel also protects a checkout.
    for parent in (path, *path.parents):
        if (parent/'pyproject.toml').is_file() and (parent/'src/meteorology').is_dir():
            raise ValueError('Data must be outside the toolkit repository.')
    return path


def temporary_root(output, product='weather'):
    output = data_path(output)
    root = output if product == 'weather' else output.parent
    token = hashlib.sha256(str(output).encode()).hexdigest()[:16]
    return data_path(root/'tmp'/(product+'-'+token))


def is_temporary_directory(path):
    path = Path(path)
    return path.name == 'tmp' and path.is_dir() and not path.is_symlink()
