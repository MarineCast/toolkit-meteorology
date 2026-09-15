"""Canonical lunar computation, build, validation, and inspection interfaces."""

__all__ = ["build_lunar", "inspect_lunar"]


def __getattr__(name: str):
    if name == "build_lunar":
        from .build import build_lunar

        return build_lunar
    if name == "inspect_lunar":
        from .inspect import inspect_lunar

        return inspect_lunar
    raise AttributeError(name)
