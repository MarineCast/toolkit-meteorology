"""Solar numerical anchors, UTC integration and recoverable companion delivery."""
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from meteorology import check_solar_cycle, get_solar_cycle
from meteorology import solar
from meteorology.artifacts import checksum_path
from .test_marine_reporting import registry


@pytest.fixture
def selection(tmp_path, monkeypatch):
    member, study, _, _ = registry(tmp_path)
    monkeypatch.setattr(solar, 'load_reporting_membership', lambda _: member)
    return member, dict(study_config=study, output_dir=tmp_path/'solar-cycle')


@pytest.mark.parametrize('stamp,lat,lon,elevation,azimuth', [
    # Frozen Astral 3.2 geometric/NOAA-Meeus reference values; no runtime dependency.
    ('2003-10-17T19:30:30Z', 39.742476, -105.1786, 39.87207442406391, 194.34258286147943),
    ('2024-06-20T20:00:00Z', 49, -125, 64.08029047662873, 168.5114481594614),
    ('2024-12-21T16:00:00Z', 49, -125, -2.4081340073754802, 123.94841151334785),
    ('2012-02-29T00:00:00Z', 49, -125, 17.318306277523206, 234.6957282900767),
])
def test_frozen_position_references(stamp, lat, lon, elevation, azimuth):
    el, az = solar.solar_position(pd.Timestamp(stamp).timestamp(), lat, lon)
    assert el == pytest.approx(elevation, abs=1e-8)
    assert az == pytest.approx(azimuth, abs=1e-8)


def test_daily_integrates_split_utc_day_and_absent_astronomical_night():
    day = date(2024, 6, 20)
    events, hours = solar.daily_cycle(day, [49], [-125])
    sunrise, sunset = events['SUNRISE_UTC'][0], events['SUNSET_UTC'][0]
    assert sunset < sunrise
    assert hours['DAYLIGHT_HOURS'][0] == pytest.approx((sunset + 86400 - sunrise)/3600)
    assert sum(v[0] for v in hours.values()) == pytest.approx(24)
    assert hours['NIGHT_HOURS'][0] == 0
    assert np.isnan(events['ASTRONOMICAL_DAWN_UTC'][0])
    assert np.isnan(events['ASTRONOMICAL_DUSK_UTC'][0])
    # Astral 3.2 references (standard refraction differs from fixed USNO horizon).
    assert abs(sunrise - (12*3600+15*60+57.090360)) < 60
    assert abs(sunset - (4*3600+27*60+23.384241)) < 60
    for i, name in enumerate(solar.EVENTS):
        if np.isfinite(events[name][0]):
            el, _ = solar.solar_position(pd.Timestamp(day, tz='UTC').timestamp()
                                        + events[name][0], 49, -125)
            assert el == pytest.approx(solar.THRESHOLDS[i//2], abs=1e-4)


def test_short_night_near_seasonal_tangency_is_retained():
    # At this latitude astronomical night lasts far less than an hour; the
    # hourly points alone cannot establish duration or absence of a crossing.
    _, hours = solar.daily_cycle(date(2024, 6, 20), [48.5], [-125])
    assert 0 < hours['NIGHT_HOURS'][0] < 1


def test_phase_boundaries_and_invalid_coordinates():
    assert list(solar._phases(np.array([0, -50/60, -1, -6, -7, -12, -13, -18, -19]))) == [
        'daylight', 'daylight', 'civil_twilight', 'civil_twilight', 'nautical_twilight',
        'nautical_twilight', 'astronomical_twilight', 'astronomical_twilight', 'night']
    for lat, lon in [(np.nan, 0), (80, 0), (49, 181)]:
        with pytest.raises(ValueError):
            solar.solar_position(1700000000, lat, lon)


def test_product_exact_support_defaults_noop_and_rebuild(selection):
    member, options = selection
    result = get_solar_cycle('2012-02-28', '2012-03-01', **options)
    report = check_solar_cycle(result.catalog_path, study_config='study')
    assert report['valid'] and report['membership_verified']
    assert report['products']['hourly']['rows'] == 72*len(member.cells)
    assert report['products']['daily']['rows'] == 3*len(member.cells)
    assert set(pq.read_table(result.daily_path)['H3_INDEX'].to_pylist()) == set(member.cells)
    before = {p.name: checksum_path(p) for p in options['output_dir'].iterdir()}
    get_solar_cycle('2012-02-29', '2012-02-29', **options)
    assert before == {p.name: checksum_path(p) for p in options['output_dir'].iterdir()}
    with pytest.raises(ValueError, match='rebuild=True'):
        get_solar_cycle('2012-02-29', '2012-03-02', **options)
    get_solar_cycle('2012-02-29', '2012-02-29', rebuild=True, **options)
    assert check_solar_cycle(result.catalog_path)['products']['daily']['rows'] == len(member.cells)


def test_default_window_and_destination(selection):
    member, options = selection
    member.study_identity['study_config']['time'] = dict(start='2024-12-21', end_exclusive='2024-12-22')
    result = get_solar_cycle(study_config=options['study_config'])
    assert result.catalog_path.parent == Path(member.study_identity['resolved_data_root'])/'Meteorology'/'solar-cycle'
    assert check_solar_cycle(result.catalog_path)['start_date'] == '2024-12-21'


def test_invalid_dates_and_unrelated_files_preserved(selection):
    _, options = selection
    for start, end in [('2027-01-01', '2027-01-02'), ('2024-02-30', '2024-03-01'),
                       ('2024-01-02', '2024-01-01')]:
        with pytest.raises(ValueError):
            get_solar_cycle(start, end, **options)
    assert not options['output_dir'].exists()
    options['output_dir'].mkdir()
    unrelated = options['output_dir']/'keep.txt'
    unrelated.write_text('preserve')
    with pytest.raises(ValueError, match='unrelated'):
        get_solar_cycle('2024-01-01', '2024-01-01', **options)
    assert unrelated.read_text() == 'preserve'


def test_checker_rejects_corrupt_bytes_and_duration_partition(selection):
    _, options = selection
    result = get_solar_cycle('2024-12-21', '2024-12-21', **options)
    table = pq.read_table(result.daily_path)
    index = table.column_names.index('DAYLIGHT_HOURS')
    pq.write_table(table.set_column(index, 'DAYLIGHT_HOURS',
                   pa.array(table['DAYLIGHT_HOURS'].to_numpy()+1)), result.daily_path)
    with pytest.raises(ValueError, match='checksum'):
        check_solar_cycle(result.catalog_path)
    catalog = json.loads(result.catalog_path.read_text())
    catalog['products']['daily'].update(sha256=checksum_path(result.daily_path), bytes=result.daily_path.stat().st_size)
    result.catalog_path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match='partition'):
        check_solar_cycle(result.catalog_path)


def test_failed_rebuild_preserves_retained_product(selection, monkeypatch):
    _, options = selection
    get_solar_cycle('2024-01-01', '2024-01-01', **options)
    before = {p.name: checksum_path(p) for p in options['output_dir'].iterdir()}
    monkeypatch.setattr(solar, '_tables', lambda *args: (_ for _ in ()).throw(RuntimeError('injected')))
    with pytest.raises(RuntimeError, match='injected'):
        get_solar_cycle('2024-01-02', '2024-01-02', rebuild=True, **options)
    assert before == {p.name: checksum_path(p) for p in options['output_dir'].iterdir()}


def test_solar_cli_visible():
    result = subprocess.run([sys.executable, '-m', 'meteorology', 'solar', '--help'],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert '--study-config' in result.stdout
