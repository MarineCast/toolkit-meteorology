"""Validation contract for canonical lunar features."""

from __future__ import annotations

import pandas as pd

from .compute import OUTPUT_COLUMNS, SYNODIC_MONTH_DAYS


def validate_lunar_illumination_features(
    frame: pd.DataFrame,
    strict: bool = True,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
) -> list[str]:
    errors: list[str] = []
    missing = [column for column in OUTPUT_COLUMNS if column not in frame.columns]
    if missing:
        errors.append(f"Missing required columns: {missing}")
    else:
        if frame.duplicated(["h3", "date"]).any():
            errors.append("Duplicate h3-date rows found.")
        nulls = [column for column in OUTPUT_COLUMNS if frame[column].isna().any()]
        if nulls:
            errors.append(f"Null values found in columns: {nulls}")
        bounds = {
            "centroid_lat": (-90.0, 90.0),
            "centroid_lon": (-180.0, 180.0),
            "lunar_age_days": (0.0, SYNODIC_MONTH_DAYS),
            "lunar_phase_angle_deg": (0.0, 360.0),
            "lunar_illumination_fraction": (0.0, 1.0),
            "moon_visible_dark_fraction": (0.0, 1.0),
            "moonlit_dark_fraction": (0.0, 1.0),
            "weight_lunar_illumination": (0.0, 1.0),
            "weight_moonlit_dark_hours": (0.0, 1.0),
        }
        for column, (lower, upper) in bounds.items():
            values = pd.to_numeric(frame[column], errors="coerce")
            if not values.between(lower, upper).all():
                errors.append(f"{column} contains values outside [{lower}, {upper}].")
        for column in (
            "night_hours",
            "moon_visible_hours",
            "moon_visible_dark_hours",
            "moonlit_dark_hours",
        ):
            values = pd.to_numeric(frame[column], errors="coerce")
            if not values.between(0.0, 26.0).all():
                errors.append(f"{column} contains values outside [0, 26].")
        if (frame["moon_visible_dark_hours"] > frame["night_hours"] + 1e-9).any():
            errors.append("moon_visible_dark_hours exceeds night_hours.")
        if (frame["moonlit_dark_hours"] > frame["night_hours"] + 1e-9).any():
            errors.append("moonlit_dark_hours exceeds night_hours.")
        dates = pd.to_datetime(frame["date"], errors="coerce")
        if dates.isna().any():
            errors.append("date contains invalid values.")
        if (
            start_date is not None
            and dates.min().normalize() != pd.Timestamp(start_date).normalize()
        ):
            errors.append("date range does not start at the expected date.")
        if end_date is not None and dates.max().normalize() != pd.Timestamp(end_date).normalize():
            errors.append("date range does not end at the expected date.")
        expected_month_day = (
            pd.to_numeric(frame["month"], errors="coerce").astype("Int64").astype(str).str.zfill(2)
            + "-"
            + pd.to_numeric(frame["day"], errors="coerce").astype("Int64").astype(str).str.zfill(2)
        )
        if not frame["month_day"].astype(str).eq(expected_month_day).all():
            errors.append("month_day does not match month/day columns.")
    if errors and strict:
        raise ValueError("Lunar feature validation failed: " + "; ".join(errors))
    return errors


__all__ = ["validate_lunar_illumination_features"]
