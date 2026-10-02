from __future__ import annotations

import math

import pytest

from meteorology.atmospheric_summaries import dewpoint_depression_c, gust_factor


def test_dewpoint_depression_preserves_zero_and_missing_inputs() -> None:
    assert dewpoint_depression_c(2.0, 2.0) == 0.0
    assert dewpoint_depression_c(5.0, 2.0) == 3.0
    assert dewpoint_depression_c(5.0, None) is None
    with pytest.raises(ValueError, match="finite"):
        dewpoint_depression_c(math.nan, 2.0)


def test_gust_factor_calm_is_undefined_not_zero() -> None:
    assert gust_factor(0.0, 0.0, 0.0) is None
    assert gust_factor(8.0, 3.0, 4.0) == 1.6
    assert gust_factor(None, 3.0, 4.0) is None
    with pytest.raises(ValueError, match="nonnegative"):
        gust_factor(-1.0, 3.0, 4.0)
