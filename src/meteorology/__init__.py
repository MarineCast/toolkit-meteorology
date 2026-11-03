"""Separate coastal Weather, Solar Cycle and Solar Radiation products."""
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


def get_solar_cycle(start_date=None, end_date=None, **options):
    """Publish the distinct calculated Solar Cycle companion."""
    from .solar import get_solar_cycle as implementation
    return implementation(start_date, end_date, **options)


def check_solar_cycle(catalog_path, **options):
    """Read-only checks of the distinct Solar Cycle product."""
    from .solar import check_solar_cycle as implementation
    return implementation(catalog_path, **options)


__all__ += ['get_solar_cycle', 'check_solar_cycle']


def get_solar_radiation(start_date=None, end_date=None, **options):
    """Publish the distinct source-qualified Solar Radiation companion."""
    from .radiation import get_solar_radiation as implementation
    return implementation(start_date, end_date, **options)


def check_solar_radiation(catalog_path, **options):
    """Read-only radiation keys, provenance, units and complete-day reductions."""
    from .radiation import check_solar_radiation as implementation
    return implementation(catalog_path, **options)


__all__ += ['get_solar_radiation', 'check_solar_radiation']
