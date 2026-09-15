"""Validation and QA summaries for daylight feature panels."""

from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = [
    "h3",
    "date",
    "year",
    "day_of_year",
    "month",
    "day",
    "month_day",
    "is_leap_day",
    "solar_day_365",
    "centroid_lat",
    "centroid_lon",
    "daylight_hours",
    "daylight_fraction",
]
WEIGHT_COLUMNS = [
    "daylight_weight_cell_norm",
    "daylight_weight_global_norm",
    "daylight_weight",
]


def validate_daylight_features(
    df: pd.DataFrame,
    strict: bool = True,
    require_date: bool = True,
) -> list[str]:
    """Validate schema and value contracts for daylight feature panels."""
    errors: list[str] = []
    required_base = (
        REQUIRED_COLUMNS
        if require_date
        else [
            "h3",
            "day_of_year",
            "month",
            "day",
            "month_day",
            "is_leap_day",
            "solar_day_365",
            "centroid_lat",
            "centroid_lon",
            "daylight_hours",
            "daylight_fraction",
        ]
    )
    required = required_base + [col for col in WEIGHT_COLUMNS if col in df.columns]
    missing = [col for col in required_base if col not in df.columns]
    if missing:
        errors.append(f"Missing required columns: {missing}")

    if not missing:
        null_cols = [col for col in required if df[col].isna().any()]
        if null_cols:
            errors.append(f"Null values found in columns: {null_cols}")

        duplicate_keys = ["h3", "date"] if require_date else ["h3", "day_of_year"]
        duplicate_count = int(df.duplicated(subset=duplicate_keys).sum())
        if duplicate_count:
            errors.append(f"Duplicate {'-'.join(duplicate_keys)} rows found: {duplicate_count}")

        lat = pd.to_numeric(df["centroid_lat"], errors="coerce")
        lon = pd.to_numeric(df["centroid_lon"], errors="coerce")
        daylight = pd.to_numeric(df["daylight_hours"], errors="coerce")
        fraction = pd.to_numeric(df["daylight_fraction"], errors="coerce")
        day_of_year = pd.to_numeric(df["day_of_year"], errors="coerce")
        month = pd.to_numeric(df["month"], errors="coerce")
        day = pd.to_numeric(df["day"], errors="coerce")
        solar_day = pd.to_numeric(df["solar_day_365"], errors="coerce")

        if not lat.between(-90.0, 90.0).all():
            errors.append("centroid_lat contains values outside [-90, 90].")
        if not lon.between(-180.0, 180.0).all():
            errors.append("centroid_lon contains values outside [-180, 180].")
        if not daylight.between(0.0, 24.0).all():
            errors.append("daylight_hours contains values outside [0, 24].")
        if not fraction.between(0.0, 1.0).all():
            errors.append("daylight_fraction contains values outside [0, 1].")
        if not day_of_year.between(1, 366).all():
            errors.append("day_of_year contains values outside [1, 366].")
        if not month.between(1, 12).all():
            errors.append("month contains values outside [1, 12].")
        if not day.between(1, 31).all():
            errors.append("day contains values outside [1, 31].")
        if not solar_day.between(1, 365).all():
            errors.append("solar_day_365 contains values outside [1, 365].")
        if "month_day" in df.columns:
            month_day = df["month_day"].astype("string")
            expected_month_day = (
                month.astype("Int64").astype(str).str.zfill(2)
                + "-"
                + day.astype("Int64").astype(str).str.zfill(2)
            )
            if not month_day.eq(expected_month_day).all():
                errors.append("month_day does not match month/day columns.")
        if "is_leap_day" in df.columns:
            leap_day = df["is_leap_day"].astype(bool)
            if not leap_day.eq(df["month_day"].astype(str).eq("02-29")).all():
                errors.append("is_leap_day does not match month_day == '02-29'.")

        for col in WEIGHT_COLUMNS:
            if col not in df.columns:
                continue
            values = pd.to_numeric(df[col], errors="coerce")
            if not values.between(0.0, 1.0).all():
                errors.append(f"{col} contains values outside [0, 1].")

    if errors and strict:
        raise ValueError("Daylight feature validation failed: " + "; ".join(errors))
    return errors


def summarize_daylight_features(df: pd.DataFrame) -> dict[str, object]:
    """Return a compact deterministic QA summary for logging or manifests."""
    summary: dict[str, object] = {
        "rows": int(len(df)),
        "columns": list(df.columns),
    }
    if "h3" in df.columns:
        summary["h3_cells"] = int(df["h3"].nunique(dropna=True))
    if "date" in df.columns:
        dates = pd.to_datetime(df["date"], errors="coerce")
        summary["start_date"] = str(dates.min().date()) if dates.notna().any() else None
        summary["end_date"] = str(dates.max().date()) if dates.notna().any() else None
        summary["dates"] = int(dates.nunique(dropna=True))
    for col in ["daylight_hours", "daylight_fraction", *WEIGHT_COLUMNS]:
        if col not in df.columns:
            continue
        values = pd.to_numeric(df[col], errors="coerce")
        summary[f"{col}_min"] = float(values.min()) if values.notna().any() else None
        summary[f"{col}_max"] = float(values.max()) if values.notna().any() else None
        summary[f"{col}_mean"] = float(values.mean()) if values.notna().any() else None
    return summary
