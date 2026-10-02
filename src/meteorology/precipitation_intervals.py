"""Exact source-accumulation interval arithmetic for candidate products."""

from __future__ import annotations

from math import isfinite

from .temporal_products import _utc


def _spatial_target(row: dict) -> tuple:
    """Return an explicit native point or mapped H3 target, never a grid alone."""
    basis = row.get("spatial_basis")
    if basis == "native_grid_point":
        index = row.get("source_grid_index")
        if not isinstance(index, int) or index < 0:
            raise ValueError("Native precipitation requires a source-grid point identity.")
        return (basis, row.get("source_grid_hash"), index)
    if basis == "h3_cell":
        cell, mapping = row.get("H3_INDEX"), row.get("mapping_identity")
        if not isinstance(cell, str) or not cell or not isinstance(mapping, str) or not mapping:
            raise ValueError("H3 precipitation requires cell and mapping identity.")
        return (basis, row.get("source_grid_hash"), cell, mapping)
    raise ValueError("Precipitation interval requires an explicit spatial target basis.")


def difference_same_run_accumulations(earlier: dict, later: dict) -> dict:
    """Difference verified 0-to-lead cumulative amounts from one forecast run.

    Callers must first verify the decoded GRIB parameter, units, and step ranges;
    record labels alone are not source evidence. Cross-run subtraction is invalid.
    """
    identity_fields = ("source_model", "source_product", "parameter", "source_grid_hash",
                       "init_time_utc")
    if any(earlier.get(name) is None or earlier.get(name) != later.get(name)
           for name in identity_fields):
        raise ValueError("Cumulative precipitation values must share a source run and grid point.")
    target = _spatial_target(earlier)
    if target != _spatial_target(later):
        raise ValueError("Cumulative precipitation values must share a spatial target.")
    if any(row.get("step_type") != "accum" or row.get("units") != "mm"
           or row.get("step_start_hour") != 0 for row in (earlier, later)):
        raise ValueError("Cumulative precipitation requires verified 0-to-lead mm accumulation.")
    init = _utc(earlier["init_time_utc"])
    first_end, second_end = (_utc(row["valid_time_utc"]) for row in (earlier, later))
    first_step, second_step = (row.get("step_end_hour") for row in (earlier, later))
    if any(not isinstance(step, int) or step < 0 for step in (first_step, second_step)):
        raise ValueError("Cumulative precipitation requires nonnegative integer lead hours.")
    if not first_end < second_end or (first_end - init).total_seconds() != first_step * 3600 \
            or (second_end - init).total_seconds() != second_step * 3600:
        raise ValueError("Cumulative step bounds and source valid times disagree.")
    first, second = (row.get("amount_mm") for row in (earlier, later))
    if any(not isinstance(value, (int, float)) or not isfinite(value) or value < 0
           for value in (first, second)) or second < first:
        raise ValueError("Cumulative precipitation is missing, negative, or reset within a run.")
    availability = max(_utc(row["available_at_utc"]) for row in (earlier, later))
    result = {
        "interval_start_utc": first_end.isoformat(), "interval_end_utc": second_end.isoformat(),
        "init_time_utc": init.isoformat(), "step_start_hour": first_step,
        "step_end_hour": second_step, "available_at_utc": availability.isoformat(),
        "source_model": earlier["source_model"], "source_product": earlier["source_product"],
        "parameter": earlier["parameter"], "source_grid_hash": earlier["source_grid_hash"],
        "spatial_basis": earlier["spatial_basis"], "step_type": "accum", "units": "mm",
        "amount_mm": second - first, "modeled_precipitation_amount_mm": second - first,
    }
    for name in ("source_grid_index", "H3_INDEX", "mapping_identity"):
        if name in earlier:
            result[name] = earlier[name]
    return result

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
    target_identity = None
    for row in ordered:
        left, right = _utc(row["interval_start_utc"]), _utc(row["interval_end_utc"])
        if left != cursor or right <= left or right > end:
            raise ValueError("Precipitation intervals have a gap, overlap, or unsupported boundary crossing.")
        identity = (row.get("source_model"), row.get("source_product"),
                    row.get("parameter"), row.get("source_grid_hash"))
        if None in identity or (source_identity is not None and identity != source_identity):
            raise ValueError("Precipitation source identity changes inside the requested interval.")
        source_identity = identity
        target = _spatial_target(row)
        if target_identity is not None and target != target_identity:
            raise ValueError("Precipitation spatial target changes inside the requested interval.")
        target_identity = target
        if _utc(row["init_time_utc"]) > left:
            raise ValueError("Precipitation initialization follows interval start.")
        available = _utc(row["available_at_utc"])
        if available < right:
            raise ValueError("Precipitation availability precedes interval end.")
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
            "source_identity": source_identity, "spatial_target": target_identity}
