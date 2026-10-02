"""Unit-explicit derived quantities for validated hourly source inputs."""

from __future__ import annotations

from math import hypot, isfinite


def dewpoint_depression_c(temperature_2m_c: float | None,
                          dewpoint_2m_c: float | None) -> float | None:
    """Matched 2 m T minus Td; null if either matched input is unavailable."""
    if temperature_2m_c is None or dewpoint_2m_c is None:
        return None
    if not isfinite(temperature_2m_c) or not isfinite(dewpoint_2m_c):
        raise ValueError("Temperature and dew point must be finite.")
    return temperature_2m_c - dewpoint_2m_c


def gust_factor(gust_ms: float | None, u_10m_ms: float | None,
                v_10m_ms: float | None, *, calm_cutoff_ms: float = 1.0) -> float | None:
    """Gust divided by matched wind magnitude; calm/near-calm is undefined."""
    if any(value is None for value in (gust_ms, u_10m_ms, v_10m_ms)):
        return None
    if not all(isfinite(value) for value in (gust_ms, u_10m_ms, v_10m_ms)):
        raise ValueError("Gust and wind components must be finite.")
    if gust_ms < 0 or calm_cutoff_ms <= 0:
        raise ValueError("Gust must be nonnegative and calm cutoff positive.")
    speed = hypot(u_10m_ms, v_10m_ms)
    return None if speed < calm_cutoff_ms else gust_ms / speed
