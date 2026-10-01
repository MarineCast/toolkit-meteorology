"""Validation contract for canonical lunar features."""

from __future__ import annotations

import pandas as pd

from ..astronomy import local_civil_day_hours
from .compute import OUTPUT_COLUMNS, SYNODIC_MONTH_DAYS


def validate_lunar_illumination_features(
    frame: pd.DataFrame,
    strict: bool = True,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
    timezone_name: str | None = None,
) -> list[str]:
    errors: list[str] = []
    missing = [column for column in OUTPUT_COLUMNS if column not in frame.columns]
    if missing:
        errors.append(f"Missing required columns: {missing}")
    else:
        if frame.duplicated(["h3", "date"]).any():
            errors.append("Duplicate h3-date rows found.")
        nullable_when_no_night = {
            "moon_visible_dark_fraction", "moonlit_dark_fraction", "weight_moonlit_dark_hours"
        }
        nulls = [column for column in OUTPUT_COLUMNS if column not in nullable_when_no_night and frame[column].isna().any()]
        if nulls:
            errors.append(f"Null values found in columns: {nulls}")
        no_night = frame["night_hours"].eq(0)
        for column in nullable_when_no_night:
            if not frame[column].isna().eq(no_night).all():
                errors.append(f"{column} must be null exactly when night_hours is zero.")
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
            if column in nullable_when_no_night:
                values = values.dropna()
            if not values.between(lower, upper).all():
                errors.append(f"{column} contains values outside [{lower}, {upper}].")
        for column in (
            "night_hours",
            "moon_visible_hours",
            "moon_visible_dark_hours",
            "moonlit_dark_hours",
        ):
            values = pd.to_numeric(frame[column], errors="coerce")
            if timezone_name:
                limits = frame["date"].astype(str).map(
                    lambda date: local_civil_day_hours(date, timezone_name)
                )
                if (values.lt(0) | values.gt(limits + 1e-8)).any():
                    errors.append(f"{column} exceeds the local civil-day duration.")
            elif not values.between(0.0, 26.0).all():
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
