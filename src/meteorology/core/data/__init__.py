from .registry import DATASETS

__all__ = ["DATASETS", "register_builtin_datasets"]
from .catalog import register_builtin_datasets

register_builtin_datasets()
