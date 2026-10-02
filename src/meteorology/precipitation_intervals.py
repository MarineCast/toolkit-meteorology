"""Exact source-accumulation interval arithmetic for candidate products."""

from __future__ import annotations

from math import isfinite

from .temporal_products import _utc

def sum_exact_precipitation_intervals(
    records: list[dict], *, start_utc: str, end_utc: str,
    as_of_utc: str | None = None,
) -> dict:
    """Sum identified nonoverlapping source amounts only across exact coverage."""
    start, end = _utc(start_utc), _utc(end_utc)
    as_of = _utc(as_of_utc) if as_of_utc is not None else None
    if not start < end:
        raise ValueError("Precipitation interval end must follow start.")
    ordered = sorted(records, key=lambda row: _utc(row["interval_start_utc"]))
    cursor = start
    amount = 0.0
    source_identity = None
    for row in ordered:
        left, right = _utc(row["interval_start_utc"]), _utc(row["interval_end_utc"])
        if left != cursor or right <= left or right > end:
            raise ValueError("Precipitation intervals have a gap, overlap, or unsupported boundary crossing.")
        identity = (row.get("source_model"), row.get("source_product"),
                    row.get("parameter"), row.get("source_grid_hash"))
        if None in identity or (source_identity is not None and identity != source_identity):
            raise ValueError("Precipitation source identity changes inside the requested interval.")
        source_identity = identity
        _utc(row["init_time_utc"])
        available = _utc(row["available_at_utc"])
        if as_of is not None and available > as_of:
            raise ValueError("Precipitation amount was unavailable at the requested as-of time.")
        if row.get("step_type") != "accum" or row.get("units") != "mm":
            raise ValueError("Precipitation amount requires verified accumulation semantics in millimetres.")
        value = row.get("amount_mm")
        if value is None or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise ValueError("Precipitation amount must be finite and nonnegative; zero is valid.")
        amount += value
        cursor = right
    if cursor != end:
        raise ValueError("Precipitation intervals do not cover the complete requested period.")
    return {"start_utc": start.isoformat(), "end_utc": end.isoformat(),
            "covered_hours": (end - start).total_seconds() / 3600,
            "interval_count": len(ordered), "modeled_precipitation_amount_mm": amount,
            "source_identity": source_identity}
