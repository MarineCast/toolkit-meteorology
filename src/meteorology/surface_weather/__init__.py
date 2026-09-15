"""Canonical HRRR acquisition, offline build, and inspection interfaces."""

__all__ = ["build_surface_weather", "download_surface_weather", "inspect_surface_weather"]


def __getattr__(name: str):
    if name == "download_surface_weather":
        from .download import download_surface_weather

        return download_surface_weather
    if name == "build_surface_weather":
        from .build import build_surface_weather

        return build_surface_weather
    if name == "inspect_surface_weather":
        from .inspect import inspect_surface_weather

        return inspect_surface_weather
    raise AttributeError(name)
