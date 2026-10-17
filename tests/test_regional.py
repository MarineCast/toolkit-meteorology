"""Offline regression specifications for the default regional API (no provider access)."""
import hashlib
import json
from pathlib import Path
import threading
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from meteorology import regional
from meteorology.regional_source import Archive


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


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(regional, 'Archive', FakeArchive)
    monkeypatch.setattr(regional, 'mapping', lambda *args: (
        np.array([0, 1]), ['851234567ffffff', '851234568ffffff'], np.zeros(2), 'fixture'))
    monkeypatch.setattr(FakeArchive, 'fail', False)
    monkeypatch.setattr(FakeArchive, 'missing_rain', False)
    monkeypatch.setattr(FakeArchive, 'invalid_rh', False)


def test_wide_outputs_and_end_hour_precipitation(tmp_path, fake):
    result = regional.get_data('2009-01-01', '2009-01-01', output_dir=tmp_path / 'public')
    assert {p.name for p in result.catalog_path.parent.iterdir()} == {
        'meteorology-hourly.parquet', 'meteorology-daily.parquet', 'catalog.json'}
    hourly = pq.read_table(result.hourly_path)
    daily = pq.read_table(result.daily_path)
    assert hourly.schema.field('DATETIME').type == pa.timestamp('us', tz='UTC')
    assert daily.schema.field('DATE').type == pa.date32()
    assert len(hourly) == 48 and len(daily) == 2
    assert len(hourly.column_names) == 12  # H3, timestamp, ten hourly metrics
    assert len(daily.column_names) == 16  # H3, date, fourteen aggregates
    h, d = hourly.to_pandas(), daily.to_pandas()
    assert not h.duplicated(['H3_INDEX', 'DATETIME']).any()
    assert not d.duplicated(['H3_INDEX', 'DATE']).any()
    assert h.PRECIPITATION_MM.iloc[0] == pytest.approx(1.)
    assert h.PRECIPITATION_MM.iloc[-1] == pytest.approx(24.)
    assert d.PRECIPITATION_MM.iloc[0] == pytest.approx(sum(range(1, 25)))
    assert d.TEMPERATURE_2M_C_MEAN.iloc[0] == pytest.approx(10.)
    assert d.WIND_SPEED_10M_MS_MEAN.iloc[0] == pytest.approx(5.)
    catalog = json.loads(result.catalog_path.read_text())
    assert catalog['h3_resolution'] == 5
    assert catalog['source_revision'].startswith('unknown')
    assert catalog['products']['daily']['sha256'] == hashlib.sha256(result.daily_path.read_bytes()).hexdigest()


def test_missing_and_unsupported_values_stay_null(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(FakeArchive, 'missing_rain', True)
    monkeypatch.setattr(FakeArchive, 'invalid_rh', True)
    result = regional.get_data('2009-01-01', '2009-01-01', output_dir=tmp_path / 'public')
    d = pq.read_table(result.daily_path).to_pandas()
    assert pd.isna(d.PRECIPITATION_MM.iloc[0])
    assert pd.isna(d.RELATIVE_HUMIDITY_2M_PCT_MEAN.iloc[0])
    assert pd.notna(d.TEMPERATURE_2M_C_MEAN.iloc[0])
    assert pd.notna(d.PRECIPITATION_MM.iloc[1])
    catalog = json.loads(result.catalog_path.read_text())
    status = catalog['products']['daily']['value_status_counts']['RELATIVE_HUMIDITY_2M_PCT_MEAN']
    assert status['OUTSIDE_SUPPORTED_RH_DOMAIN'] == 1


def test_exact_metric_and_hourly_companion(tmp_path, fake):
    result = regional.get_metric('TEMPERATURE_2M_C_MIN', '2009-01-01', '2009-01-01',
                                 output_dir=tmp_path / 'public')
    assert pq.read_schema(result.daily_path).names == ['H3_INDEX', 'DATE', 'TEMPERATURE_2M_C_MIN']
    assert pq.read_schema(result.hourly_path).names == ['H3_INDEX', 'DATETIME', 'TEMPERATURE_2M_C']
    assert set(regional.selections('WIND_SPEED_10M_MS')['daily']) == {
        'WIND_SPEED_10M_MS_MEAN', 'WIND_SPEED_10M_MS_MAX'}


def test_failure_preserves_published_pair(tmp_path, fake, monkeypatch):
    result = regional.get_data('2009-01-01', '2009-01-01', output_dir=tmp_path / 'public')
    original = {p: p.read_bytes() for p in (result.hourly_path, result.daily_path, result.catalog_path)}
    monkeypatch.setattr(FakeArchive, 'fail', True)
    with pytest.raises(RuntimeError, match='Simulated'):
        regional.get_data('2009-01-01', '2009-01-02', output_dir=tmp_path / 'public')
    assert all(p.read_bytes() == value for p, value in original.items())


def test_invalid_request_does_not_acquire(tmp_path, monkeypatch):
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('Unexpected acquisition'))
    with pytest.raises(ValueError, match='Unknown metric'):
        regional.get_metric('not-a-metric', '2009-01-01', '2009-01-01')
    with pytest.raises(ValueError, match='on or before'):
        regional.get_data('2009-02-01', '2009-01-01')
    with pytest.raises(ValueError, match='separate'):
        regional.get_data('2009-01-01', '2009-01-01', output_dir=tmp_path, cache_dir=tmp_path / 'cache')


def bare_archive(tmp_path, fetcher, cap=1024):
    archive = Archive.__new__(Archive)
    archive.root = Path(tmp_path)
    archive.started = time.monotonic()
    archive.max_seconds = 60
    archive.requests = archive.bytes = 0
    archive.cap = cap
    archive.lock = threading.Lock()
    archive.used = {}
    archive.fetcher = fetcher
    return archive


def test_source_cache_reuse_and_corruption_detection(tmp_path):
    calls = []
    archive = bare_archive(tmp_path, lambda name, cap: calls.append(name) or b'fixture')
    assert archive.object('tp/8.10.20', limit=100) == b'fixture'
    assert archive.object('tp/8.10.20', limit=100) == b'fixture'
    assert calls == ['tp/8.10.20']
    assert archive.bytes == 7
    (tmp_path / 'objects/tp/8.10.20').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='Corrupt'):
        archive.object('tp/8.10.20', limit=100)


def test_transfer_budget_rejects_before_network(tmp_path):
    archive = bare_archive(tmp_path, lambda *a: pytest.fail('Unexpected transfer'), cap=50)
    with pytest.raises(ValueError, match='cap'):
        archive.object('t2m/8.10.20', limit=100)


def test_cube_keeps_next_midnight_at_time_block_boundary():
    archive = Archive.__new__(Archive)
    archive.snapshot = 'fixture'
    # A whole-day boundary lands exactly at index 67584 in this synthetic axis.
    origin = pd.Timestamp('2009-01-01', tz='UTC')
    archive.times = (origin.value // 10**9) + (np.arange(67609) - 67560) * 3600
    archive.coords = np.array([[48., -124.]])
    blocks = []

    def block(parameter, index):
        blocks.append(index)
        return (np.arange(67584) + index * 67584).reshape(-1, 1)

    archive.block = block
    cube = archive.cube(origin.date(), ['tp'])
    assert cube['values'][0, 0, 0] == 67560
    assert cube['values'][0, 24, 0] == 67584
    assert set(blocks) == {0, 1}


def test_regional_block_crops_axes_and_reuses_verified_local_array(tmp_path):
    archive = bare_archive(tmp_path, lambda *a: pytest.fail('No network expected'))
    archive.times = np.arange(10)
    archive.coords = np.array([[48., -124.], [48., -123.75],
                              [48.25, -124.], [48.25, -123.75]])
    archive.ys = np.array([1, 2])
    archive.xs = np.array([1, 2])
    archive.workers = 1
    archive.blocks = {}
    archive.local = tmp_path / 'arrays'
    archive.local.mkdir()
    source = np.arange(10 * 4 * 4, dtype='<f4').reshape(10, 4, 4)
    decoded = []

    def decode(name):
        decoded.append(name)
        archive.used[name] = dict(sha256='fixture')
        return source

    archive.decode = decode
    expected = source[:, 1:3, 1:3].reshape(10, 4)
    assert np.array_equal(archive.block('t2m', 0), expected)
    archive.blocks = {}
    assert np.array_equal(archive.block('t2m', 0), expected)
    assert decoded == ['t2m/0.0.0']
    path = archive.local / 't2m-0.npy'
    archive.blocks = {}
    with path.open('r+b') as stream:
        stream.seek(-1, 2)
        stream.write(b'\xff')
    with pytest.raises(ValueError, match='Corrupt cached regional array'):
        archive.block('t2m', 0)


def test_vector_hourly_matches_reference_reducer_with_missing_invalid_and_calm():
    from datetime import date
    from meteorology.backfill_compute import day_frames
    rng = np.random.default_rng(1729)
    parameters = ['t2m', 'd2m', 'u10', 'v10', 'msl', 'tcc', 'tp']
    values = np.array([rng.normal(center, spread, (25, 8)) for center, spread in
                       [(280, 2), (275, 2), (0, 3), (0, 3), (101000, 500), (.5, .1), (.001, .0001)]])
    values[0, 3, 2] = np.nan
    values[1, 4, 3] = 300
    values[2:4, 5, 4] = 0
    values[2, 6, 5] = np.nan
    values[6, 24, 7] = np.nan
    day = date(2009, 1, 1)
    cube = dict(values=values, parameters=np.array(parameters),
                times=pd.date_range(day, periods=25, freq='h', tz='UTC').as_unit('ns').asi8,
                coordinates=np.column_stack([np.arange(8), np.arange(8)]),
                metadata=dict(grid_hash='fixture', evidence={}))
    spatial = (np.array([7, 0, 1, 2, 3, 4, 5, 6, 7]), [str(i) for i in range(9)],
               np.zeros(9), 'fixture')
    specs = regional.selections(None)['hourly']
    counts = {}
    actual = regional.hourly_day(cube, day, specs, spatial, counts).to_pandas()
    expected, expected_counts = {}, {}
    request = dict(variables=list(regional.VARIABLES), frequency='hourly')
    for frame in day_frames(cube, None, day, request, spatial, {}):
        name = frame.METRIC.iloc[0]
        expected.setdefault(name, []).append(frame.VALUE.to_numpy())
        target = expected_counts.setdefault(name, {})
        for status, count in frame.VALUE_STATUS.value_counts().items():
            target[str(status)] = target.get(str(status), 0) + int(count)
    for name, blocks in expected.items():
        np.testing.assert_array_equal(actual[name].to_numpy(), np.concatenate(blocks))
    assert counts == expected_counts


def test_missing_terminal_midnight_is_explicit_partial_not_zero():
    archive = Archive.__new__(Archive)
    archive.snapshot = 'fixture'
    stamps = pd.date_range('2009-01-01', periods=24, freq='h', tz='UTC').as_unit('ns')
    archive.times = stamps.asi8 // 10**9
    archive.coords = np.array([[48., -124.]])
    archive.block = lambda p, i: np.full((24, 1), 273.15 if p == 't2m' else .001)
    with pytest.raises(ValueError, match='boundary'):
        archive.cube(stamps[0].date(), ['t2m', 'tp'])
    cube = archive.cube(stamps[0].date(), ['t2m', 'tp'], allow_missing_boundary=True)
    spatial = (np.array([0]), ['cell'], np.zeros(1), 'fixture')
    specs = regional.metric_specs('daily')
    counts = {}
    table = regional.wide_day(cube, stamps[0].date(),
        {k: v for k, v in specs.items() if v['variable'] in ('temperature', 'precipitation')},
        'daily', spatial, counts)
    assert table['TEMPERATURE_2M_C_MEAN'].to_pylist() == pytest.approx([0.])
    assert table['PRECIPITATION_MM'].to_pylist() == [None]
    assert counts['PRECIPITATION_MM'] == {'PARTIAL': 1}
    archive.times = archive.times[:-1]
    with pytest.raises(ValueError, match='boundary'):
        archive.cube(stamps[0].date(), ['tp'], allow_missing_boundary=True)
