import h3
import pandas as pd
import pytest
from meteorology.daily_matrix import combine_frames


def test_native_support_values_and_nulls():
    r4 = h3.latlng_to_cell(48, -123, 4)
    r5 = h3.latlng_to_cell(48, -123, 5)
    out, fields = combine_frames(
        {
            "daylight": pd.DataFrame(
                {"DATE": ["2020-01-01"], "H3_INDEX": [r4], "DAYLIGHT_HOURS": [8.0]}
            ),
            "surface_weather": pd.DataFrame(
                {"DATE": ["2020-01-01"], "H3_INDEX": [r5], "PRECIP_MM_DAY_ESTIMATE": [0.0]}
            ),
        }
    )
    indexed = out.set_index("H3_INDEX")
    assert indexed.loc[r4, "daylight__DAYLIGHT_HOURS"] == 8
    assert indexed.loc[r5, "surface_weather__PRECIP_MM_DAY_ESTIMATE"] == 0
    assert pd.isna(indexed.loc[r4, "surface_weather__PRECIP_MM_DAY_ESTIMATE"])
    assert fields["daylight__DAYLIGHT_HOURS"]["native_resolution"] == 4


@pytest.mark.parametrize("failure", ["duplicate", "resolution", "date"])
def test_reject_incompatible_inputs(failure):
    f = pd.DataFrame({"DATE": ["2020-01-01"], "H3_INDEX": [h3.latlng_to_cell(48, -123, 5)]})
    g = f.copy()
    if failure == "duplicate":
        g = pd.concat([g, g])
    if failure == "resolution":
        g["H3_INDEX"] = h3.latlng_to_cell(48, -123, 4)
    if failure == "date":
        g["DATE"] = "2020-01-02"
    with pytest.raises(ValueError):
        combine_frames({"surface_weather": f, "lunar": g})
