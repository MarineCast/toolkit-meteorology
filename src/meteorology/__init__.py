"""Approved coastal ERA5 weather: acquire, reproduce and check."""
from ._version import __version__


def get_weather(start_date=None, end_date=None, variable_list=None, **options):
    """Publish inclusive UTC dates for the approved coastal registry."""
    from .regional import get_weather as implementation
    return implementation(start_date, end_date, variable_list, **options)


def check_weather(catalog_path, **options):
    """Read-only contract checks; pass study_config to verify external membership."""
    from .regional import check_weather as implementation
    return implementation(catalog_path, **options)


def validate_product(catalog_path):
    """Compatibility spelling for checking the supported weather catalog."""
    return check_weather(catalog_path)


def __getattr__(name):
    if name == 'ALLOWED_VARIABLES':
        from .regional import ALLOWED_VARIABLES
        return ALLOWED_VARIABLES
    raise AttributeError(name)


__all__ = ['__version__', 'get_weather', 'check_weather', 'validate_product', 'ALLOWED_VARIABLES']
