"""Native-grain, publication-aware RONI context from externally retained CPC rows.

No source acquisition is performed here. A current CPC table without archived
release vintages is retrospective context and cannot answer historical as-of queries.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite

RONI_PROVIDER = "NOAA_CPC"
RONI_METHOD = "RONI_ERSSTv6_1991_2020"
RONI_SOURCE = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/"
SEASONS = ("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ")


def _utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("RONI publication/retrieval times must be UTC-aware.")
    return result.astimezone(timezone.utc)


def validate_roni_rows(rows: list[dict]) -> list[dict]:
    """Validate source-specific seasonal values and distinct publication vintages."""
    seen = set()
    validated = []
    for row in rows:
        if row.get("provider") != RONI_PROVIDER or row.get("method") != RONI_METHOD:
            raise ValueError("Climate row is not the declared CPC ERSSTv6 RONI series.")
        if row.get("source_url") != RONI_SOURCE:
            raise ValueError("RONI source URL identity differs from the declared CPC table.")
        year, season = row.get("year"), row.get("season")
        if not isinstance(year, int) or season not in SEASONS:
            raise ValueError("RONI rows require a valid year and overlapping three-month season.")
        value = row.get("value_c")
        if not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError("RONI value must be finite degrees Celsius.")
        published = _utc(row.get("published_at_utc"))
        retrieved = _utc(row.get("retrieved_at_utc"))
        if retrieved is None:
            raise ValueError("RONI retrieval time is required independently of publication time.")
        if published is not None and published > retrieved:
            raise ValueError("RONI publication cannot follow the recorded retrieval.")
        key = (year, season, published)
        if key in seen:
            raise ValueError("Duplicate RONI season and publication vintage.")
        seen.add(key)
        validated.append({**row, "published_at_utc": row.get("published_at_utc"), "availability_status": (
            "publication_vintage_recorded" if published is not None else "retrospective_only"
        )})
    return validated


def select_roni_asof(rows: list[dict], *, year: int, season: str,
                     as_of_utc: str | None = None) -> dict | None:
    """Select a seasonal revision without backdating a later-published value."""
    validated = validate_roni_rows(rows)
    candidates = [row for row in validated if row["year"] == year and row["season"] == season]
    if as_of_utc is not None:
        cutoff = _utc(as_of_utc)
        candidates = [row for row in candidates if row.get("published_at_utc") is not None
                      and _utc(row["published_at_utc"]) <= cutoff]
    if not candidates:
        return None
    if as_of_utc is None:
        # Unknown publication order is not silently interpreted as an old vintage.
        if len(candidates) != 1:
            raise ValueError("Multiple RONI revisions require an explicit as-of time.")
        return candidates[0]
    return max(candidates, key=lambda row: _utc(row["published_at_utc"]))
