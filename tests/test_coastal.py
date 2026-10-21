"""Offline retained-product publication tests."""
from types import SimpleNamespace
import numpy as np
import pandas as pd

import pyarrow.parquet as pq
import pytest

from meteorology import coastal, regional


class FakeArchive:
    fail = False
    missing_rain = False
    invalid_rh = False

    def __init__(self, root, bbox, **options):
        self.coords = np.array([[48., -124.], [48.25, -124.]])
        self.times = pd.date_range('2009-01-01', periods=73, freq='h', tz='UTC').as_unit('ns').asi8 // 10**9
        self.snapshot = 'offline-fixture'
        self.used = {}
        self.requests = self.bytes = 0

    def checkpoint(self):
        pass

    def cube(self, day, parameters):
        if self.fail:
            raise RuntimeError('Simulated acquisition failure')
        stamps = pd.date_range(day, periods=25, freq='h', tz='UTC').as_unit('ns')
        source = dict(t2m=np.full((25, 2), 283.15), d2m=np.full((25, 2), 278.15),
                      u10=np.full((25, 2), 3.), v10=np.full((25, 2), 4.),
                      msl=np.full((25, 2), 101325.), tcc=np.full((25, 2), .5),
                      tp=np.repeat(np.arange(25)[:, None], 2, axis=1) / 1000.)
        if self.missing_rain:
            source['tp'][24, 0] = np.nan
        if self.invalid_rh:
            source['d2m'][2, 0] = 300.
        return dict(values=np.array([source[p] for p in parameters]), parameters=np.array(parameters),
                    coordinates=self.coords, times=stamps.asi8,
                    metadata=dict(grid_hash='fixture', evidence={}))



def test_preserves_values_and_rejects_source_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(regional, 'Archive', FakeArchive)
    monkeypatch.setattr(regional, 'mapping', lambda *args: ([0, 1], ['a', 'b'], None, None))
    monkeypatch.setattr(FakeArchive, 'invalid_rh', False)
    monkeypatch.setattr(FakeArchive, 'fail', False)
    monkeypatch.setattr(FakeArchive, 'missing_rain', False)
    first = regional.get_data('2009-01-01', '2009-01-01', output_dir=tmp_path/'first')
    last = regional.get_data('2009-01-02', '2009-01-02', output_dir=tmp_path/'last')
    member = SimpleNamespace(cells=('b',), resolution=5, artifact_sha256='membership',
        mask_sha256='mask', mask_revision='revision', study_identity={'study_config': {
            'domain': {'bbox_wgs84': list(regional.DEFAULT_BBOX)},
            'time': {'start': '2009-01-01', 'end_exclusive': '2010-01-01'}}})
    monkeypatch.setattr(coastal, 'load_reporting_membership', lambda _: member)
    result = coastal.publish_coastal([last.catalog_path.parent, first.catalog_path.parent],
        'study', tmp_path/'out', tmp_path/'control')
    assert result['products']['daily']['rows'] == 2
    assert result['products']['hourly']['rows'] == 48
    assert len(result['source_epochs']) == 2
    assert result['unavailable_interval']['start'] == '2009-01-03'
    table = pq.read_table(tmp_path/'out/meteorology-daily.parquet')
    assert table['H3_INDEX'].to_pylist() == ['b', 'b']
    assert table['TEMPERATURE_2M_C_MEAN'].to_pylist() == pytest.approx([10., 10.])
    with pytest.raises(ValueError, match='contiguous'):
        coastal.publish_coastal([first.catalog_path.parent, first.catalog_path.parent],
            'study', tmp_path/'bad', tmp_path/'bad-control')
