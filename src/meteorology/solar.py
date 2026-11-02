"""Deterministic coastal Solar Cycle, independent of weather acquisition.

NOAA/Meeus solar coordinates; USNO sea-level rise/set and twilight definitions.
Angles are geometric solar-centre angles at exact UTC instants. Daily durations
integrate threshold crossings over the UTC day, including light at midnight.
"""
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil

import h3
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ._version import __version__
from .artifacts import checksum_path
from .core.artifacts import TransactionalFamilyPublisher, atomic_write_json
from .marine_reporting import load_reporting_membership
from .run_settings import log

METHOD = 'noaa-meeus-geometric-utc-solar-v1'
PRODUCT = 'solar-cycle'
THRESHOLDS = (-50 / 60, -6., -12., -18.)
PHASES = ('daylight', 'civil_twilight', 'nautical_twilight', 'astronomical_twilight', 'night')
EVENTS = ('SUNRISE_UTC', 'SUNSET_UTC', 'CIVIL_DAWN_UTC', 'CIVIL_DUSK_UTC',
          'NAUTICAL_DAWN_UTC', 'NAUTICAL_DUSK_UTC',
          'ASTRONOMICAL_DAWN_UTC', 'ASTRONOMICAL_DUSK_UTC')
HOURS = ('DAYLIGHT_HOURS', 'CIVIL_TWILIGHT_HOURS', 'NAUTICAL_TWILIGHT_HOURS',
         'ASTRONOMICAL_TWILIGHT_HOURS', 'NIGHT_HOURS')
SOURCES = ['https://gml.noaa.gov/grad/solcalc/calcdetails.html',
           'https://aa.usno.navy.mil/faq/RST_defs']


@dataclass(frozen=True)
class SolarResult:
    hourly_path: Path
    daily_path: Path
    catalog_path: Path


def _terms(seconds):
    """Julian-century Meeus apparent longitude, obliquity, declination and EOT."""
    t = (np.asarray(seconds, dtype=float) / 86400 + 2440587.5 - 2451545) / 36525
    mean_lon = np.radians((280.46646 + t * (36000.76983 + .0003032 * t)) % 360)
    anomaly = np.radians(357.52911 + t * (35999.05029 - .0001537 * t))
    eccentricity = .016708634 - t * (.000042037 + .0000001267 * t)
    centre = (np.sin(anomaly) * (1.914602 - t * (.004817 + .000014 * t))
              + np.sin(2 * anomaly) * (.019993 - .000101 * t)
              + np.sin(3 * anomaly) * .000289)
    omega = np.radians(125.04 - 1934.136 * t)
    apparent_lon = mean_lon + np.radians(centre - .00569 - .00478 * np.sin(omega))
    obliquity = np.radians(23 + (26 + (21.448 - t * (46.815 + t *
                             (.00059 - t * .001813))) / 60) / 60
                             + .00256 * np.cos(omega))
    declination = np.arcsin(np.sin(obliquity) * np.sin(apparent_lon))
    y = np.tan(obliquity / 2) ** 2
    eot = 4 * np.degrees(y * np.sin(2 * mean_lon) - 2 * eccentricity * np.sin(anomaly)
              + 4 * eccentricity * y * np.sin(anomaly) * np.cos(2 * mean_lon)
              - .5 * y * y * np.sin(4 * mean_lon)
              - 1.25 * eccentricity ** 2 * np.sin(2 * anomaly))
    return declination, eot


def solar_position(seconds, latitude, longitude):
    """Return geometric elevation and clockwise-from-true-north azimuth, degrees.

    Inputs broadcast as NumPy arrays; time is Unix seconds in UTC. No atmospheric,
    terrain, observer-height or weather correction is applied. Azimuth at the
    zenith/nadir is undefined and returned as NaN.
    """
    seconds, latitude, longitude = np.broadcast_arrays(
        np.asarray(seconds, dtype=float), np.asarray(latitude, dtype=float),
        np.asarray(longitude, dtype=float))
    if (not all(np.isfinite(a).all() for a in (seconds, latitude, longitude))
            or np.any(np.abs(latitude) > 72) or np.any(np.abs(longitude) > 180)
            or np.any(seconds < -2208988800) or np.any(seconds >= 4133980800)):
        raise ValueError('Solar calculation requires 1900–2100 UTC, latitude ±72°, longitude ±180°.')
    declination, eot = _terms(seconds)
    lat = np.radians(latitude)
    angle = np.radians(((seconds % 86400 / 60 + 4 * longitude + eot) / 4) % 360 - 180)
    sine = np.sin(lat) * np.sin(declination) + np.cos(lat) * np.cos(declination) * np.cos(angle)
    elevation = np.degrees(np.arcsin(np.clip(sine, -1, 1)))
    azimuth = (np.degrees(np.arctan2(np.sin(angle), np.cos(angle) * np.sin(lat)
                                  - np.tan(declination) * np.cos(lat))) + 180) % 360
    return elevation, np.where(np.abs(sine) >= 1 - 1e-14, np.nan, azimuth)


def _extreme(midnight, guess, latitude, longitude, sign):
    # Bracket the actual extremum, rather than sampling hourly: very short
    # twilight/night intervals near seasonal tangencies must not disappear.
    left, right = guess - 1800, guess + 1800
    for _ in range(24):
        a, b = (2 * left + right) / 3, (left + 2 * right) / 3
        ea = solar_position(midnight + a, latitude, longitude)[0] * sign
        eb = solar_position(midnight + b, latitude, longitude)[0] * sign
        left, right = np.where(ea < eb, a, left), np.where(ea < eb, right, b)
    return np.clip((left + right) / 2, 0, 86400)


def daily_cycle(day, latitude, longitude):
    """UTC-day event seconds and five mutually exclusive phase durations.

    Null event times mean no crossing within this UTC date; phase durations are
    still known (including genuine zero). Sunset can precede sunrise in UTC.
    """
    latitude, longitude = np.broadcast_arrays(np.atleast_1d(latitude), np.atleast_1d(longitude))
    midnight = pd.Timestamp(day, tz='UTC').timestamp()
    _, eot = _terms(midnight + 43200)
    noon = (43200 - 240 * longitude - 60 * eot) % 86400
    high = _extreme(midnight, noon, latitude, longitude, 1)
    low = _extreme(midnight, (noon + 43200) % 86400, latitude, longitude, -1)
    knots = np.sort(np.stack([np.zeros_like(high), high, low, np.full_like(high, 86400)]), axis=0)
    left, right = knots[:-1], knots[1:]
    el = solar_position(midnight + left, latitude, longitude)[0]
    er = solar_position(midnight + right, latitude, longitude)[0]
    events, above_hours = {}, []
    for k, threshold in enumerate(THRESHOLDS):
        before, after = el >= threshold, er >= threshold
        crossing = before != after
        rows, cols = np.where(crossing)
        lo, hi = left[crossing].copy(), right[crossing].copy()
        ascending = after[crossing]
        for _ in range(20):
            mid = (lo + hi) / 2
            above = solar_position(midnight + mid, latitude[cols], longitude[cols])[0] >= threshold
            move_left = above != ascending
            lo, hi = np.where(move_left, mid, lo), np.where(move_left, hi, mid)
        roots = np.full(left.shape, np.nan)
        roots[rows, cols] = (lo + hi) / 2
        durations = np.where(before & after, right - left,
                     np.where(crossing, np.where(before, roots - left, right - roots), 0))
        above_hours.append(durations.sum(axis=0) / 3600)
        for direction in (0, 1):
            selected = crossing & (after if direction == 0 else before)
            if np.any(selected.sum(axis=0) > 1):
                raise ValueError('Multiple solar crossings exceed this daily event contract.')
            values = np.where(selected, roots, np.inf).min(axis=0)
            events[EVENTS[2 * k + direction]] = np.where(np.isfinite(values), values, np.nan)
    durations = np.diff(np.vstack([np.zeros_like(high), *above_hours, np.full_like(high, 24)]), axis=0)
    return events, dict(zip(HOURS, durations))


def _schema(frequency):
    stamp = pa.timestamp('us', tz='UTC')
    if frequency == 'hourly':
        return pa.schema([('H3_INDEX', pa.string()), ('DATETIME', stamp),
            ('SOLAR_ELEVATION_DEG', pa.float64()), ('SOLAR_AZIMUTH_DEG', pa.float64()),
            ('SOLAR_PHASE', pa.string())])
    return pa.schema([('H3_INDEX', pa.string()), ('DATE', pa.date32()),
        *[(name, stamp) for name in EVENTS], *[(name, pa.float64()) for name in HOURS]])


def _phases(elevation):
    return np.asarray(PHASES)[np.sum(elevation[..., None] < np.asarray(THRESHOLDS), axis=-1)]


def _tables(day, cells):
    latitude, longitude = np.asarray([h3.cell_to_latlng(c) for c in cells]).T
    stamps = pd.date_range(day, periods=24, freq='h', tz='UTC')
    seconds = stamps.as_unit('s').asi8[:, None]
    elevation, azimuth = solar_position(seconds, latitude, longitude)
    hourly = pa.Table.from_pydict(dict(H3_INDEX=list(cells) * 24,
        DATETIME=stamps.repeat(len(cells)), SOLAR_ELEVATION_DEG=elevation.ravel(),
        SOLAR_AZIMUTH_DEG=pa.array(azimuth.ravel(), from_pandas=True),
        SOLAR_PHASE=_phases(elevation).ravel()), schema=_schema('hourly'))
    events, durations = daily_cycle(day, latitude, longitude)
    data = dict(H3_INDEX=cells, DATE=[day] * len(cells))
    midnight = pd.Timestamp(day, tz='UTC')
    for name, seconds in events.items():
        data[name] = pd.to_datetime(midnight.value + np.nan_to_num(seconds) * 1e9, utc=True)
        data[name] = pa.array(data[name].as_unit('us'), mask=~np.isfinite(seconds),
                              type=pa.timestamp('us', tz='UTC'))
    data.update(durations)
    return {'hourly': hourly, 'daily': pa.Table.from_pydict(data, schema=_schema('daily'))}


def _identity(member):
    return dict(method=METHOD, product=PRODUCT, timezone='UTC',
        membership_sha256=member.artifact_sha256, mask_sha256=member.mask_sha256,
        mask_revision=member.mask_revision, reporting_cell_count=len(member.cells),
        h3_resolution=member.resolution, spatial_support='WGS84 H3 cell centre, sea-level unobstructed horizon',
        position='geometric solar centre; no atmospheric refraction correction',
        azimuth='degrees clockwise from true north; undefined at zenith/nadir',
        thresholds_deg=dict(zip(PHASES[:4], THRESHOLDS)),
        daily_interval='[00:00, next 00:00) UTC; events belong to this UTC date',
        event_null='no crossing within UTC date; not missing observations',
        hourly_time='instant at exact UTC hour; not an hourly average',
        units=dict(SOLAR_ELEVATION_DEG='degrees above geometric horizon',
                   SOLAR_AZIMUTH_DEG='degrees clockwise from true north',
                   SOLAR_PHASE='category', **{name: 'UTC timestamp' for name in EVENTS},
                   **{name: 'hours within UTC day' for name in HOURS}),
        sources=SOURCES)


def _directory(output):
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ValueError('Solar output must be a dedicated regular directory.')
    if output.exists() and any(p.is_symlink() or p.name not in
            {'catalog.json', 'solar-cycle-hourly.parquet', 'solar-cycle-daily.parquet'} for p in output.iterdir()):
        raise ValueError('Solar output contains unrelated files or symlinks; preserved.')


def get_solar_cycle(start_date=None, end_date=None, *, study_config=None, output_dir=None,
                    rebuild=False, verbosity=False):
    """Build hourly angles and daily phases for inclusive approved UTC dates.

    Dates default to the entire configured study, independently of weather .env
    settings or the ERA5 frontier. Output defaults to Data/Meteorology/solar-cycle.
    A validated covered request is a no-op; changing the range requires rebuild.
    """
    if type(rebuild) is not bool or type(verbosity) is not bool:
        raise ValueError('Solar rebuild and verbosity must be booleans.')
    supplied = [date.fromisoformat(str(d)) if d is not None else None for d in (start_date, end_date)]
    if all(supplied) and supplied[0] > supplied[1]:
        raise ValueError('Solar start_date must be on or before end_date.')
    member = load_reporting_membership(study_config)
    window = member.study_identity['study_config']['time']
    first = supplied[0] or date.fromisoformat(window['start'])
    last = supplied[1] or date.fromisoformat(window['end_exclusive']) - timedelta(days=1)
    if first > last or str(first) < window['start'] or str(last) >= window['end_exclusive']:
        raise ValueError('Solar dates must lie inside the approved study interval.')
    coordinates = np.asarray([h3.cell_to_latlng(c) for c in member.cells])
    if not len(coordinates):
        raise ValueError('Solar Cycle requires nonempty reporting membership.')
    solar_position([pd.Timestamp(first, tz='UTC').timestamp(),
                    pd.Timestamp(last + timedelta(days=1), tz='UTC').timestamp()],
                   coordinates[:, 0, None], coordinates[:, 1, None])
    data_root = Path(member.study_identity['resolved_data_root'])
    raw_output = Path(output_dir).expanduser().absolute() if output_dir is not None else data_root/'Meteorology'/PRODUCT
    if any(p.is_symlink() for p in (raw_output, *raw_output.parents)):
        raise ValueError('Solar output ancestors must not be symlinks.')
    output = raw_output.resolve()
    _directory(output)
    identity = hashlib.sha256(str(output).encode()).hexdigest()[:16]
    control = data_root.parent/'.marinecast-work'/'meteorology'/('solar-' + identity)
    if control.is_relative_to(output) or output.is_relative_to(control):
        raise ValueError('Solar delivery and working directories must be separate.')
    result = SolarResult(output/'solar-cycle-hourly.parquet', output/'solar-cycle-daily.parquet', output/'catalog.json')
    code_hash = checksum_path(Path(__file__))
    with TransactionalFamilyPublisher(control) as publisher:
        _directory(output)
        if output.exists() and any(output.iterdir()):
            existing = check_solar_cycle(result.catalog_path, _member=member)
            old = json.loads(result.catalog_path.read_text())
            if not rebuild:
                if (old['code_sha256'] == code_hash and old['start_date'] <= str(first)
                        and old['end_date'] >= str(last)):
                    log(verbosity, 'Solar Cycle already covers requested dates; output unchanged.')
                    return result
                raise ValueError('Solar range or code changed; use rebuild=True to replace the product.')
            del existing
        days = (last - first).days + 1
        estimate = days * len(member.cells) * (24 * 80 + 240)
        if shutil.disk_usage(control).free < estimate + 512 * 1024**2:
            raise ValueError('Insufficient disk headroom for Solar Cycle staging.')
        atomic_write_json(control/'staging-manifest.json', dict(owner='meteorology-solar-cycle',
            task='Solar Cycle production', staging=str(publisher.staging),
            retained_output=str(output), expected_peak_bytes=estimate,
            source_inputs='approved registry and deterministic equations; no downloads',
            cleanup_status='transaction owns temporary candidates; removed after commit or rollback'), overwrite=True)
        meta = _identity(member)
        paths = {f: publisher.stage_path(getattr(result, f + '_path')) for f in ('hourly', 'daily')}
        counts = {f: dict.fromkeys(_schema(f).names, 0) for f in paths}
        with ExitStack() as stack:
            writers = {f: stack.enter_context(pq.ParquetWriter(paths[f], _schema(f).with_metadata(
                {b'solar_cycle': json.dumps(meta, sort_keys=True).encode()}), compression='zstd')) for f in paths}
            for i in range(days):
                if i % 365 == 0:
                    if shutil.disk_usage(control).free < 512 * 1024**2:
                        raise ValueError('Solar staging stopped before exhausting disk space.')
                    log(verbosity, f'Solar Cycle {i + 1:,}/{days:,} UTC days')
                for f, table in _tables(first + timedelta(days=i), member.cells).items():
                    writers[f].write_table(table)
                    for name in table.column_names:
                        counts[f][name] += table[name].null_count
        catalog = dict(meta, schema_version=1, display_name='Solar Cycle', start_date=str(first),
            end_date=str(last), software_version=__version__, code_sha256=code_hash,
            run_date_utc=datetime.now(timezone.utc).isoformat(),
            environment=dict(python=platform.python_version(), numpy=np.__version__,
                             pandas=pd.__version__, pyarrow=pa.__version__, h3=h3.__version__),
            study_identity=member.study_identity,
            qualification='calculated astronomical context; not irradiance, visibility, glare or observed detection',
            products={f: dict(file=getattr(result, f + '_path').name, sha256=checksum_path(p),
                bytes=p.stat().st_size, rows=days * len(member.cells) * (24 if f == 'hourly' else 1),
                null_counts=counts[f]) for f, p in paths.items()})
        candidate = publisher.stage_manifest(result.catalog_path, catalog)
        check_solar_cycle(candidate, _member=member, _files=paths)
        publisher.publish()
        check_solar_cycle(result.catalog_path, _member=member)
        atomic_write_json(control/'staging-manifest.json', dict(owner='meteorology-solar-cycle',
            task='Solar Cycle production', retained_output=str(output), code_sha256=code_hash,
            products=catalog['products'], cleanup_status='committed; task-owned candidates removed on context exit'), overwrite=True)
    return result


def check_solar_cycle(catalog_path, *, study_config=None, _member=None, _files=None):
    """Read-only checks of hashes, support, UTC keys, units/conventions and phases."""
    path = Path(catalog_path)
    catalog = json.loads(path.read_text())
    if (catalog.get('schema_version') != 1 or catalog.get('method') != METHOD
            or catalog.get('product') != PRODUCT or catalog.get('timezone') != 'UTC'):
        raise ValueError('Unsupported Solar Cycle catalog.')
    member = _member or (load_reporting_membership(study_config) if study_config is not None else None)
    if member is not None and any(catalog.get(k) != v for k, v in _identity(member).items()):
        raise ValueError('Solar catalog differs from approved membership or method conventions.')
    first, last = [date.fromisoformat(catalog[k]) for k in ('start_date', 'end_date')]
    days = (last - first).days + 1
    if days < 1:
        raise ValueError('Invalid Solar Cycle date range.')
    expected_cells = set(member.cells) if member is not None else None
    summary = {}
    for f in ('hourly', 'daily'):
        product = catalog['products'][f]
        if product['file'] != f'solar-cycle-{f}.parquet':
            raise ValueError('Unexpected Solar Cycle filename.')
        source = _files[f] if _files is not None else path.parent/product['file']
        if source.is_symlink() or source.stat().st_size != product['bytes'] or checksum_path(source) != product['sha256']:
            raise ValueError('Solar Cycle size/checksum mismatch.')
        q = pq.ParquetFile(source)
        metadata = json.loads((q.schema_arrow.metadata or {}).get(b'solar_cycle', b'{}'))
        if (not q.schema_arrow.equals(_schema(f), check_metadata=False)
                or q.num_row_groups != days or any(catalog.get(k) != v for k, v in metadata.items())
                or metadata.get('method') != METHOD):
            raise ValueError('Solar Cycle schema, metadata or row groups differ.')
        nulls = dict.fromkeys(q.schema_arrow.names, 0)
        rows = 0
        for i in range(days):
            table = q.read_row_group(i)
            frame = table.to_pandas()
            key = 'DATETIME' if f == 'hourly' else 'DATE'
            day = first + timedelta(days=i)
            cells = set(frame.H3_INDEX)
            if expected_cells is None:
                expected_cells = cells
            stamps = (set(pd.date_range(day, periods=24, freq='h', tz='UTC')) if f == 'hourly' else {day})
            if (cells != expected_cells or len(cells) != catalog['reporting_cell_count']
                    or len(frame) != len(cells) * len(stamps) or set(frame[key]) != stamps
                    or frame[['H3_INDEX', key]].isna().any().any()
                    or frame.duplicated(['H3_INDEX', key]).any()):
                raise ValueError('Missing, duplicate or off-grid Solar Cycle UTC keys.')
            if f == 'hourly':
                el, az = frame.SOLAR_ELEVATION_DEG, frame.SOLAR_AZIMUTH_DEG
                if (not np.isfinite(el).all() or not el.between(-90, 90).all()
                        or np.isinf(az).any() or not az.dropna().between(0, 360, inclusive='left').all()
                        or not np.array_equal(frame.SOLAR_PHASE, _phases(el.to_numpy()))):
                    raise ValueError('Invalid solar angles or phase classifications.')
            else:
                durations = frame[list(HOURS)].to_numpy()
                if (not np.isfinite(durations).all() or (durations < -1e-10).any()
                        or not np.allclose(durations.sum(axis=1), 24, rtol=0, atol=1e-9)):
                    raise ValueError('Solar phase durations must partition 24 UTC hours.')
                for name in EVENTS:
                    stamps = frame[name].dropna()
                    if not (stamps.dt.date == day).all():
                        raise ValueError('Solar event outside its UTC date.')
            for name in nulls:
                nulls[name] += table[name].null_count
            rows += len(frame)
        if rows != product['rows'] or nulls != product['null_counts']:
            raise ValueError('Solar Cycle row/null accounting mismatch.')
        summary[f] = dict(rows=rows, null_counts=nulls)
    return dict(valid=True, membership_verified=member is not None, start_date=str(first),
                end_date=str(last), reporting_cell_count=len(expected_cells), products=summary)


def is_solar_directory(path):
    """Recognize the one named companion allowed beside weather; never follow links."""
    path = Path(path)
    if path.name != PRODUCT or path.is_symlink() or not path.is_dir():
        return False
    try:
        _directory(path)
        if {p.name for p in path.iterdir()} != {'catalog.json', 'solar-cycle-hourly.parquet',
                                               'solar-cycle-daily.parquet'}:
            return False
        catalog = json.loads((path/'catalog.json').read_text())
        return catalog.get('product') == PRODUCT and catalog.get('method') == METHOD
    except (OSError, ValueError):
        return False
