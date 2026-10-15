"""Simple historical weather API: ECMWF ARCO to wide hourly/daily H3 Parquets."""
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .artifacts import checksum_path
from ._version import __version__
from .backfill_compute import BASE, WIND, day_frames, mapping, series
from .core.artifacts import TransactionalFamilyPublisher
from .regional_source import Archive, URL, utcnow

DEFAULT_BBOX = (-129.7, 45.9, -121.5, 51.5)
VARIABLES = ('temperature', 'dewpoint', 'relative_humidity', 'wind',
             'cloud_cover', 'pressure', 'precipitation')
METHOD = 'ecmwf-arco-wide-h3-utc-v1'


@dataclass(frozen=True)
class RegionalResult:
    """Paths to the three public artifacts; no entire-history DataFrame in memory."""
    hourly_path: Path
    daily_path: Path
    catalog_path: Path


def metric_specs(frequency):
    specs = {}
    for variable in VARIABLES:
        for metric, units, parameters, aggregates in WIND if variable == 'wind' else [BASE[variable]]:
            for aggregate in aggregates if frequency == 'daily' else ('instant',):
                name = metric if aggregate in ('sum', 'instant') else metric + '_' + aggregate.upper()
                specs[name] = dict(variable=variable, units=units, parameters=parameters,
                                   aggregate=aggregate)
    return specs


def selections(metric):
    """Family aliases select both grains; exact columns select their applicable grain."""
    all_specs = {f: metric_specs(f) for f in ('hourly', 'daily')}
    if metric is None:
        return all_specs
    if metric in VARIABLES:
        return {f: {k: v for k, v in specs.items() if v['variable'] == metric}
                for f, specs in all_specs.items()}
    if not any(metric in specs for specs in all_specs.values()):
        raise ValueError('Unknown metric. Use a family name or an exact catalog metric column.')
    # A daily aggregate has its instantaneous hourly companion; an hourly metric
    # selects that quantity's supported daily aggregates, not unrelated metrics.
    base = next(name for name in all_specs['hourly']
                if metric == name or metric.startswith(name + '_'))
    return {
        'hourly': {base: all_specs['hourly'][base]},
        'daily': {name: spec for name, spec in all_specs['daily'].items()
                  if (name == metric if metric not in all_specs['hourly']
                      else name == base or name.startswith(base + '_'))},
    }


def validate(start_date, end_date, bbox, workers, transfer_bytes, max_seconds):
    first, last = date.fromisoformat(str(start_date)), date.fromisoformat(str(end_date))
    if first > last:
        raise ValueError('start_date must be on or before end_date.')
    bounds = np.asarray(bbox, dtype=float)
    if bounds.shape != (4,) or not np.isfinite(bounds).all():
        raise ValueError('bbox must be finite west, south, east, north.')
    w, s, e, n = bounds
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError('bbox must be a non-wrapping WGS84 rectangle.')
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('workers must be 1..4.')
    if type(transfer_bytes) is not int or transfer_bytes <= 0:
        raise ValueError('transfer_bytes must be a positive integer.')
    if not np.isfinite(max_seconds) or not 0 < max_seconds <= 86400:
        raise ValueError('max_seconds must be in (0, 86400].')
    return first, last, bounds.tolist()


def wide_day(cube, day, specs, frequency, spatial, counters):
    """Consume long reducer frames immediately; hold only a single wide UTC day."""
    if frequency == 'hourly':
        return hourly_day(cube, day, specs, spatial, counters)
    cells = spatial[1]
    n = len(cells)
    columns = {name: np.full(n, np.nan) for name in specs}
    request = dict(variables=list(dict.fromkeys(v['variable'] for v in specs.values())),
                   frequency=frequency)
    # The shared reducer's lineage is transient here. ARCO object evidence lives
    # in the private archive; do not publish its CDS labels or fabricated expver.
    lineage = {}
    for frame in day_frames(cube, None, day, request, spatial, lineage):
        name = frame.METRIC.iloc[0]
        if name not in specs:
            continue
        columns[name] = frame.VALUE.to_numpy()
        counts = counters.setdefault(name, {})
        for status, count in frame.VALUE_STATUS.value_counts().items():
            counts[str(status)] = counts.get(str(status), 0) + int(count)
        lineage.clear()
    data = {'H3_INDEX': pa.array(cells, type=pa.string()),
            'DATE': pa.array([day] * n, type=pa.date32())}
    for name, values in columns.items():
        flat = values.ravel()
        data[name] = pa.array(flat, mask=~np.isfinite(flat), type=pa.float64())
    return pa.table(data)


def hourly_day(cube, day, specs, spatial, counters):
    """Vectorized hourly counterpart of day_frames, retaining its null policies."""
    ids, cells, _, _ = spatial
    stamps = pd.date_range(day, periods=24, freq='h', tz='UTC')
    parameters = {p for spec in specs.values() for p in spec['parameters']}
    inputs = {p: series(cube, None, p, stamps + pd.Timedelta(hours=int(p == 'tp')))[0]
              for p in parameters}
    data = {
        'H3_INDEX': pa.array(cells * 24, type=pa.string()),
        'DATETIME': pa.array(stamps.repeat(len(cells)), type=pa.timestamp('us', tz='UTC')),
    }
    for name, spec in specs.items():
        fields = spec['parameters']
        valid = np.logical_and.reduce([np.isfinite(inputs[p]) for p in fields])
        invalid = np.zeros_like(valid)
        calm = np.zeros_like(valid)
        if name.startswith('WIND_SPEED'):
            values = np.hypot(inputs['u10'], inputs['v10'])
        elif name.startswith('WIND_DIRECTION'):
            u, v = inputs['u10'], inputs['v10']
            values = np.degrees(np.arctan2(-u, -v)) % 360
            calm = np.hypot(u, v) <= 1e-12
        elif name.startswith('RELATIVE_HUMIDITY'):
            t, td = inputs['t2m'] - 273.15, inputs['d2m'] - 273.15
            values = 100 * np.exp(17.67 * td / (td + 243.5) - 17.67 * t / (t + 243.5))
            invalid = valid & (~np.isfinite(values) | (values < 0) |
                               (values > 100.01) | (td < -123.15))
        else:
            parameter = fields[0]
            values = inputs[parameter].copy()
            if parameter in {'t2m', 'd2m'}:
                values -= 273.15
            elif parameter == 'msl':
                values /= 100
            elif parameter == 'tcc':
                values *= 100
            elif parameter == 'tp':
                values *= 1000
        good = valid & ~invalid & ~calm
        sampled = values[:, ids].ravel()
        data[name] = pa.array(sampled, mask=~good[:, ids].ravel(), type=pa.float64())
        counts = counters.setdefault(name, {})
        for label, mask in (
            ('COMPLETE', good),
            ('UNAVAILABLE', ~valid),
            ('UNDEFINED_CALM_MEAN', calm & valid & ~invalid),
            ('OUTSIDE_SUPPORTED_RH_DOMAIN', invalid),
        ):
            total = int(np.count_nonzero(mask[:, ids]))
            if total:
                counts[label] = counts.get(label, 0) + total
    return pa.table(data)


def get_data(start_date, end_date, *, bbox=DEFAULT_BBOX, output_dir='data/meteorology',
             cache_dir=None, workers=4, transfer_bytes=8 * 1024**3, max_seconds=3600,
             metric_name=None):
    """Acquire/reuse regional ERA5 and publish hourly/daily wide H3 R5 Parquets.

    Inclusive UTC dates. Output contains exactly two Parquets and catalog.json in
    a dedicated directory. Source cache is a sibling by default. No network runs
    on import. Exact daily metrics include their hourly companion column.
    """
    first, last, bbox = validate(start_date, end_date, bbox, workers, transfer_bytes, max_seconds)
    selected = selections(metric_name)
    parameters = sorted({p for specs in selected.values() for spec in specs.values()
                         for p in spec['parameters']})
    output = Path(output_dir).expanduser().resolve()
    cache = (Path(cache_dir).expanduser().resolve() if cache_dir is not None
             else output.parent / '.meteorology-cache')
    if output == cache or output in cache.parents or cache in output.parents:
        raise ValueError('Output and private cache must be separate directories.')
    # Separate control namespace keeps the public directory to three files, while
    # all writers to this output share one recoverable publication journal.
    control = output.parent / ('.meteorology-publication-' +
                               hashlib.sha256(str(output).encode()).hexdigest()[:16])
    result = RegionalResult(output / 'meteorology-hourly.parquet',
                            output / 'meteorology-daily.parquet', output / 'catalog.json')
    expected = {p.name for p in (result.hourly_path, result.daily_path, result.catalog_path)}
    if output.exists() and any(p.name not in expected for p in output.iterdir()):
        raise ValueError('Choose a dedicated output directory; unrelated files are preserved.')
    # Cache lock prevents duplicate downloads and conflicting source receipts.
    with TransactionalFamilyPublisher._exclusive_parent(cache):
        archive = Archive(cache, bbox, workers=workers, transfer_bytes=transfer_bytes,
                          max_seconds=max_seconds)
        end_second = int(pd.Timestamp(last, tz='UTC').timestamp()) + 86400
        start_second = int(pd.Timestamp(first, tz='UTC').timestamp())
        if start_second < archive.times[0] or end_second > archive.times[-1]:
            raise ValueError('Dates plus precipitation boundary exceed cached source coverage; '
                             'use a fresh cache to discover a newer vintage.')
        grid = dict(coordinates=archive.coords,
                    metadata=dict(grid_hash=hashlib.sha256(archive.coords.tobytes()).hexdigest()))
        spatial = mapping(grid, bbox, 'h3', cache / 'mapping')
        counts = {'hourly': {}, 'daily': {}}
        rows = {'hourly': 0, 'daily': 0}
        static = dict(method=METHOD, software_version=__version__, source=URL,
                      source_metadata_sha256=archive.snapshot,
                      h3_resolution=5, bbox=bbox, timezone='UTC',
                      sampling='nearest native ERA5 quarter-degree grid point',
                      effective_resolution='approximately 31 km; H3 adds no weather detail',
                      source_revision='unknown; final ERA5 and preliminary ERA5T not distinguished',
                      source_forecast_init_lead='unavailable in this ARCO representation',
                      hourly_time='DATETIME is UTC hour start; precipitation is the following hour total',
                      daily_time='DATE is UTC calendar day',
                      null_policy='complete support required; unsupported RH and calm direction are null',
                      attribution='Copernicus Climate Change Service / ECMWF, ERA5, CC BY 4.0')
        with TransactionalFamilyPublisher(control) as publisher:
            paths = {'hourly': publisher.stage_path(result.hourly_path),
                     'daily': publisher.stage_path(result.daily_path)}
            writers = {}
            try:
                for stamp in pd.date_range(first, last, freq='D'):
                    archive.checkpoint()
                    day = stamp.date()
                    cube = archive.cube(day, parameters)
                    for frequency in ('hourly', 'daily'):
                        table = wide_day(cube, day, selected[frequency], frequency, spatial,
                                         counts[frequency])
                        metadata = dict(static, frequency=frequency,
                                        columns={k: v['units'] for k, v in selected[frequency].items()})
                        table = table.replace_schema_metadata(
                            {b'meteorology': json.dumps(metadata, sort_keys=True).encode()})
                        if shutil.disk_usage(control).free < table.nbytes * 2 + 64 * 1024**2:
                            raise ValueError('Insufficient disk to stage the regional output.')
                        if frequency not in writers:
                            writers[frequency] = pq.ParquetWriter(paths[frequency], table.schema,
                                                                 compression='zstd')
                        writers[frequency].write_table(table, row_group_size=65536)
                        rows[frequency] += len(table)
            finally:
                for writer in writers.values():
                    writer.close()
            products = {}
            for frequency, path in paths.items():
                schema = pq.read_schema(path)
                products[frequency] = dict(file=f'meteorology-{frequency}.parquet',
                    sha256=checksum_path(path), rows=rows[frequency], bytes=path.stat().st_size,
                    columns=[dict(name=f.name, dtype=str(f.type),
                                  units=selected[frequency].get(f.name, {}).get('units')) for f in schema],
                    value_status_counts=counts[frequency])
            evidence = dict(metadata_sha256=archive.snapshot, objects=archive.used)
            evidence_bytes = json.dumps(evidence, sort_keys=True).encode()
            evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
            evidence_path = cache / 'evidence' / (evidence_hash + '.json')
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            if not evidence_path.exists():
                evidence_path.write_bytes(evidence_bytes)
            catalog = dict(schema_version=1, run_date_utc=utcnow(), start_date=str(first),
                           end_date=str(last), **static, source_evidence_sha256=evidence_hash,
                           source_coverage_end=str(archive.times[-1].astype('datetime64[s]')),
                           products=products, acquisition=dict(requests=archive.requests,
                                                              response_bytes=archive.bytes))
            publisher.stage_manifest(result.catalog_path, catalog)
            publisher.publish()
    return result


def get_metric(metric_name, start_date, end_date, **options):
    """Publish one metric family or exact catalog column using the same local cache."""
    return get_data(start_date, end_date, metric_name=metric_name, **options)
