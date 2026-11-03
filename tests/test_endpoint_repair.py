import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from meteorology.endpoint_repair import repair_precipitation


def fixture():
    h = pd.DataFrame({'H3_INDEX': ['a'] * 24,
        'DATETIME': pd.date_range('2026-10-10', periods=24, freq='h', tz='UTC'),
        'PRECIPITATION_MM': [1.] * 23 + [np.nan], 'OTHER': [42.] * 24})
    d = pd.DataFrame({'H3_INDEX': ['a'], 'DATE': [pd.Timestamp('2026-10-10').date()],
                      'PRECIPITATION_MM': [np.nan], 'OTHER': [42.]})
    fresh = h.copy()
    fresh['PRECIPITATION_MM'] = [999.] * 23 + [2.]
    return {f: pa.Table.from_pandas(v) for f, v in [('hourly', h), ('daily', d)]}, pa.Table.from_pandas(fresh)


def test_repair_preserves_old_hours_and_other_metrics():
    old, fresh = fixture()
    result = repair_precipitation(old, fresh)
    assert result['hourly']['PRECIPITATION_MM'].to_pylist() == [1.] * 23 + [2.]
    assert result['daily']['PRECIPITATION_MM'].to_pylist() == [25.]
    for frequency in old:
        for column in old[frequency].column_names:
            if column != 'PRECIPITATION_MM':
                assert old[frequency][column].equals(result[frequency][column])
    with pytest.raises(ValueError, match='one missing'):
        repair_precipitation(result, fresh)


def test_missing_support_fails_without_mutation():
    old, fresh = fixture()
    with pytest.raises(ValueError, match='support'):
        repair_precipitation(old, old['hourly'])
    assert old['hourly']['PRECIPITATION_MM'].null_count == 1
