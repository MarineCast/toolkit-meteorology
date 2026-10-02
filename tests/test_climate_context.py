from __future__ import annotations

import pytest

from meteorology.climate_context import (
    RONI_METHOD, RONI_PROVIDER, RONI_SOURCE, select_roni_asof,
    validate_roni_rows,
)


def _row(value: float, publication: str | None) -> dict:
    return dict(provider=RONI_PROVIDER, method=RONI_METHOD, source_url=RONI_SOURCE,
                year=2024, season="DJF", value_c=value,
                published_at_utc=publication, retrieved_at_utc="2026-10-02T00:00:00Z")


def test_roni_retrospective_value_cannot_be_backdated() -> None:
    row = _row(0.0, None)
    assert validate_roni_rows([row])[0]["availability_status"] == "retrospective_only"
    assert select_roni_asof([row], year=2024, season="DJF",
                            as_of_utc="2024-04-01T00:00:00Z") is None
    assert select_roni_asof([row], year=2024, season="DJF")["value_c"] == 0.0


def test_roni_revisions_obey_publication_boundary() -> None:
    rows = [_row(0.2, "2024-03-05T12:00:00Z"), _row(0.3, "2024-04-05T12:00:00Z")]
    assert select_roni_asof(rows, year=2024, season="DJF",
                            as_of_utc="2024-03-05T11:59:59Z") is None
    assert select_roni_asof(rows, year=2024, season="DJF",
                            as_of_utc="2024-03-05T12:00:00Z")["value_c"] == 0.2
    assert select_roni_asof(rows, year=2024, season="DJF",
                            as_of_utc="2024-04-06T00:00:00Z")["value_c"] == 0.3
    with pytest.raises(ValueError, match="explicit as-of"):
        select_roni_asof(rows, year=2024, season="DJF")


def test_roni_identity_and_duplicates_are_rejected() -> None:
    with pytest.raises(ValueError, match="not the declared"):
        validate_roni_rows([dict(_row(0.1, None), method="ONI")])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_roni_rows([_row(0.1, None), _row(0.2, None)])
