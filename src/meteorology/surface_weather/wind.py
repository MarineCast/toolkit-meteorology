"""Shared scientific checks for daily earth-relative wind vectors."""

from __future__ import annotations

import numpy as np
import pandas as pd


def validate_daily_wind_vectors(frame: pd.DataFrame) -> None:
    """Require speed and FROM direction to agree with the mean U/V vector."""

    u = pd.to_numeric(frame["U_WIND_10M_MS_MEAN"], errors="coerce").to_numpy(dtype=float)
    v = pd.to_numeric(frame["V_WIND_10M_MS_MEAN"], errors="coerce").to_numpy(dtype=float)
    speed = pd.to_numeric(frame["WIND_VECTOR_SPEED_10M_MS"], errors="coerce").to_numpy(dtype=float)
    direction = pd.to_numeric(
        frame["WIND_DIRECTION_FROM_10M_DEG"], errors="coerce"
    ).to_numpy(dtype=float)
    if not np.isfinite(np.column_stack((u, v, speed))).all():
        raise ValueError("Daily wind vector components or speed are non-finite.")
    if not np.allclose(np.hypot(u, v), speed, rtol=0, atol=1e-8):
        raise ValueError("Daily wind vector speed differs from its mean components.")
    calm = speed <= 1e-12
    if not np.array_equal(calm, np.isnan(direction)):
        raise ValueError("Daily wind direction must be null exactly for calm mean vectors.")
    active = direction[~calm]
    if not np.isfinite(active).all() or ((active < 0) | (active >= 360)).any():
        raise ValueError("Daily wind direction is non-finite or outside [0, 360).")
    expected = (np.degrees(np.arctan2(-u[~calm], -v[~calm])) + 360.0) % 360.0
    error = ((active - expected + 180.0) % 360.0) - 180.0
    if not np.allclose(error, 0.0, rtol=0, atol=1e-6):
        raise ValueError("Daily wind direction differs from its mean components.")
