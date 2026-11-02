"""Strict null-only repair of a documented terminal precipitation interval."""
import numpy as np
import pandas as pd
import pyarrow as pa


def repair_precipitation(original, fresh):
    """Keep all retained values; replace one missing hour/cell and its daily sum."""
    name = 'PRECIPITATION_MM'
    hourly, daily = original['hourly'], original['daily']
    h = hourly.to_pandas()
    d = daily.to_pandas()
    new = fresh.to_pandas().set_index(['H3_INDEX', 'DATETIME'])[name]
    missing = h[name].isna()
    if (len(h) != 24 * len(d) or missing.sum() != len(d)
            or not d[name].isna().all()
            or not (h.loc[missing, 'DATETIME'].dt.hour == 23).all()
            or h.loc[missing, 'H3_INDEX'].duplicated().any()):
        raise ValueError('Repair requires one missing 23:00 hour and null daily sum per cell.')
    keys = pd.MultiIndex.from_frame(h.loc[missing, ['H3_INDEX', 'DATETIME']])
    values = new.reindex(keys).to_numpy()
    if not np.isfinite(values).all():
        raise ValueError('Missing next-midnight source support; product preserved.')
    h.loc[missing, name] = values
    sums = h.groupby('H3_INDEX')[name].agg(lambda x: np.sum(x.to_numpy()))
    out = {}
    for frequency, table, values in (
            ('hourly', hourly, h[name].to_numpy()),
            ('daily', daily, sums.reindex(d['H3_INDEX']).to_numpy())):
        out[frequency] = table.set_column(table.column_names.index(name), name,
                                         pa.array(values, type=pa.float64()))
    return out


def validate_preserved(retained, staged, repair_day, old_first):
    """Compare all retained rows before promotion, ignoring only repaired nulls."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    repair_index = (repair_day - old_first).days if repair_day else -1
    name = 'PRECIPITATION_MM'
    for frequency, source in retained.items():
        candidate = pq.ParquetFile(staged[frequency])
        for index in range(source.num_row_groups):
            old = source.read_row_group(index)
            new = candidate.read_row_group(index)
            if index != repair_index:
                equal = old.equals(new, check_metadata=False)
            else:
                equal = old.drop([name]).equals(new.drop([name]), check_metadata=False)
                valid = pc.is_valid(old[name])
                equal = equal and old[name].filter(valid).equals(new[name].filter(valid))
            if not equal:
                raise ValueError('Retained weather changed outside the authorized missing interval.')
