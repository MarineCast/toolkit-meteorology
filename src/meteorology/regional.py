"""Simple historical weather API: ECMWF ARCO to wide hourly/daily H3 Parquets."""
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .artifacts import checksum_path
from ._version import __version__
from .compute import BASE, WIND, day_frames, mapping, series
from .core.artifacts import TransactionalFamilyPublisher
from .regional_source import Archive, URL, utcnow
from .run_settings import VARIABLES, log
from .storage import data_path, temporary_root, is_temporary_directory
from .marine_reporting import select_reporting_membership, selection_metadata

DEFAULT_BBOX = (-129.7, 45.9, -121.5, 51.5)
METHOD = 'ecmwf-arco-wide-h3-utc-v1'
COASTAL_METHOD = 'era5-retained-exact-coastal-r5-v1'
ALLOWED_VARIABLES = VARIABLES


def work_root(output):
    """Keep staging, sources and stable lock inodes under the product's tmp/."""
    return temporary_root(output)


def source_root(output, cache_dir=None):
    # An explicit cache_dir is a work base, never a shared cache to sweep.
    control = work_root(output)
    return (data_path(cache_dir) / control.name
            if cache_dir is not None else control / 'downloads')


def _clear_owned_cache(cache, output):
    expected = dict(owner='meteorology-transient-sources-v1', destination=str(output))
    if cache.is_symlink() or json.loads((cache / 'owner.json').read_text()) != expected:
        raise ValueError('Source workspace ownership mismatch; preserved.')
    for name in ('arrays', 'objects', 'evidence', 'mapping'):
        path = cache / name
        if path.is_symlink():
            raise ValueError('Symlinked source workspace; preserved.')
        if path.exists():
            shutil.rmtree(path)


def _clean_completed_sources(cache, output):
    """Retry cleanup after a validated no-op, without opening source archives."""
    if cache.exists():
        with TransactionalFamilyPublisher._exclusive_parent(cache):
            _clear_owned_cache(cache, output)


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
    # Source-object evidence remains in the pinned private archive.
    for frame in day_frames(cube, None, day, request, spatial):
        name = frame.METRIC.iloc[0]
        if name not in specs:
            continue
        columns[name] = frame.VALUE.to_numpy()
        counts = counters.setdefault(name, {})
        for status, count in frame.VALUE_STATUS.value_counts().items():
            counts[str(status)] = counts.get(str(status), 0) + int(count)
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


def _claim_owned_cache(cache, output):
    """Claim only an empty source child, or verify its existing owner marker."""
    if cache.is_symlink():
        raise ValueError('Refusing symlinked source workspace.')
    marker = cache / 'owner.json'
    expected_owner = dict(owner='meteorology-transient-sources-v1', destination=str(output))
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text()) != expected_owner:
            raise ValueError('Source workspace ownership mismatch.')
    elif {p.name for p in cache.iterdir()} <= {'.publication.lock'}:
        marker.write_text(json.dumps(expected_owner))
    else:
        raise ValueError('Refusing unowned source workspace.')


def _plan_queries(first, last, old, selected, parameters, repair_day):
    """Plan new date ranges, full-range metric backfill, and optional repair."""
    old_first = date.fromisoformat(old['start_date']) if old else None
    old_last = date.fromisoformat(old['end_date']) if old else None
    missing = ([(a, b) for a, b in (
        (first, min(last, old_first - timedelta(days=1))),
        (max(first, old_last + timedelta(days=1)), last),
    ) if a <= b] if old else [(first, last)])
    backfill = {f: {name: spec for name, spec in selected[f].items()
                   if name not in {c['name'] for c in old['products'][f]['columns']}}
                for f in selected} if old else {f: {} for f in selected}
    backfill_parameters = sorted({p for specs in backfill.values()
                                  for spec in specs.values() for p in spec['parameters']})
    queries = [(a, b, parameters, selected) for a, b in missing]
    if backfill_parameters:
        queries.append((old_first, old_last, backfill_parameters, backfill))
    if repair_day:
        queries.append((repair_day, repair_day, ['tp'], {}))
    return missing, backfill, backfill_parameters, queries


def _weather_metadata(archive, bbox, membership, old):
    """Describe support and provenance independently of the table-writing loop."""
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
    if membership is not None:
        static.update(method=COASTAL_METHOD, source_method=METHOD,
                      study_identity=membership.study_identity,
                      reporting_cell_count=len(membership.cells),
                      membership_sha256=membership.artifact_sha256,
                      mask_sha256=membership.mask_sha256, mask_revision=membership.mask_revision,
                      selection='exact approved R5 membership; direct native source reduction',
                      availability=dict(kind='retrospective_reanalysis',
                          historical_available_at_utc=None,
                          forecast_eligibility='not established'))
    if membership is not None:
        static.update(selection_metadata(membership))
    if old:
        static.pop('source_metadata_sha256', None)
        static['provenance'] = 'catalog source_epochs identifies each date vintage'
    return static


def _align_backfill(table, added, names):
    """Join new columns by exact H3/time keys, preserving retained row order."""
    keys = table.column_names[:2]
    old_keys = table.select(keys).to_pandas().set_index(keys).index
    new_keys = added.select(keys).to_pandas().set_index(keys).index
    positions = new_keys.get_indexer(old_keys)
    if np.any(positions < 0):
        raise ValueError('Metric backfill differs from retained UTC keys.')
    for name in names:
        table = table.append_column(name, added[name].take(pa.array(positions)))
    return table


def get_data(start_date, end_date, *, bbox=DEFAULT_BBOX, output_dir=None,
             cache_dir=None, workers=4, transfer_bytes=8 * 1024**3, max_seconds=3600,
             allow_incomplete_final_day=False,
             variable_list=None, membership=None, verbosity=False, existing=None, publisher=None,
             _discard_cache=False, repair_terminal_precipitation=False, env_file=None):
    """Acquire/reuse regional ERA5 and publish hourly/daily wide H3 R5 Parquets.

    Inclusive UTC dates. Output contains exactly two Parquets and catalog.json in
    a dedicated directory. Source cache is a sibling by default. No network runs
    on import. The public coastal entry point supplies validated membership.
    """
    first, last, bbox = validate(start_date, end_date, bbox, workers, transfer_bytes, max_seconds)
    selected = {f: metric_specs(f) for f in ('hourly', 'daily')}
    if variable_list is not None:
        variables = allowed_variables(variable_list)
        selected = {f: {k: v for k, v in specs.items() if v['variable'] in variables}
                    for f, specs in selected.items()}
    parameters = sorted({p for specs in selected.values() for spec in specs.values()
                         for p in spec['parameters']})
    if output_dir is None:
        raise ValueError('Internal exporter requires an explicit output_dir.')
    output = data_path(output_dir)
    control = work_root(output)
    owned_cache = cache_dir is None or _discard_cache
    if owned_cache and cache_dir is not None and Path(cache_dir).is_symlink():
        raise ValueError('Refusing symlinked source workspace.')
    cache = (data_path(cache_dir) if cache_dir is not None
             else source_root(output))
    cache = data_path(cache)
    if output == cache or cache in output.parents or (output in cache.parents and not cache.is_relative_to(control)):
        raise ValueError('Output and private cache must be separate directories.')
    # Separate control namespace keeps weather to three files, while
    # all writers to this output share one recoverable publication journal.
    result = RegionalResult(output / 'meteorology-hourly.parquet',
                            output / 'meteorology-daily.parquet', output / 'catalog.json')
    expected = {p.name for p in (result.hourly_path, result.daily_path, result.catalog_path)}
    from .companions import is_meteorology_companion
    if output.exists() and any(p.name not in expected and not is_meteorology_companion(p) and not is_temporary_directory(p)
                               for p in output.iterdir()):
        raise ValueError('Choose a dedicated output directory; unrelated files are preserved.')
    started = time.perf_counter()
    query_seconds = processing_seconds = 0.0
    # Cache lock prevents duplicate downloads and conflicting source receipts.
    with TransactionalFamilyPublisher._exclusive_parent(cache):
        if owned_cache:
            _claim_owned_cache(cache, output)
        old = existing or {}
        old_first = date.fromisoformat(old['start_date']) if old else None
        old_last = date.fromisoformat(old['end_date']) if old else None
        repair_day = old_last if repair_terminal_precipitation and old.get('incomplete_final_day') else None
        if repair_day and old.get('partial_metric_coverage') != [dict(
                date=str(repair_day), metric='PRECIPITATION_MM', expected_hours=24,
                valid_hours=23, value_status='PARTIAL',
                missing_interval_end_utc=str(repair_day + timedelta(days=1)) + 'T00:00:00Z')]:
            raise ValueError('Repair requires exactly one documented terminal precipitation interval.')
        missing, backfill, backfill_parameters, queries = _plan_queries(
            first, last, old, selected, parameters, repair_day)
        missing_days = sum((b - a).days + 1 for a, b in missing)
        ranges = ', '.join(f'{a} through {b}' for a, b in missing) or 'none'
        log(verbosity, f'Missing dates: {missing_days:,} UTC day{"s" if missing_days != 1 else ""}; {ranges}')
        additions = sorted({spec['variable'] for specs in backfill.values() for spec in specs.values()})
        log(verbosity, 'New metrics to backfill: ' + (', '.join(additions) or 'none') +
            (f' across {old_first} through {old_last}' if additions else ''))
        for a, b, fields, scope in queries:
            log(verbosity, f'Query plan: UTC {a} through {b}; native fields {", ".join(fields)}')
        log(verbosity, f'Opening regional source/cache at {json.dumps(str(cache))}')
        log(verbosity, f'ECMWF ARCO endpoint: {URL}')
        metadata_started = time.perf_counter()
        archive = Archive(cache, bbox, workers=workers, transfer_bytes=transfer_bytes,
                          max_seconds=max_seconds, verbosity=verbosity, env_file=env_file)
        query_seconds += time.perf_counter() - metadata_started
        log(verbosity, f'Source metadata/cache ready in {query_seconds:.2f}s; '
            f'coverage ends {archive.times[-1].astype("datetime64[s]")} UTC')
        for a, b, fields, scope in queries:
            start_second = int(pd.Timestamp(a, tz='UTC').timestamp())
            end_second = int(pd.Timestamp(b, tz='UTC').timestamp()) + 86400
            boundary = end_second - (3600 if 'tp' not in fields or
                (allow_incomplete_final_day and b == last and a != repair_day) else 0)
            if start_second < archive.times[0] or boundary > archive.times[-1]:
                raise ValueError('Dates plus precipitation boundary exceed cached source coverage; '
                                 'use a fresh cache to discover a newer vintage.')
        final_parameters = backfill_parameters if old and last == old_last else parameters
        final_end_second = int(pd.Timestamp(last, tz='UTC').timestamp()) + 86400
        new_partial = bool('tp' in final_parameters and final_end_second > archive.times[-1])
        grid = dict(coordinates=archive.coords,
                    metadata=dict(grid_hash=hashlib.sha256(archive.coords.tobytes()).hexdigest()))
        spatial = mapping(grid, bbox, cache / 'mapping')
        if membership is not None:
            ids, cells, distances, method = spatial
            lookup = {cell: i for i, cell in enumerate(cells)}
            if not set(membership.cells) <= lookup.keys():
                raise ValueError('Approved reporting cells extend outside the source envelope.')
            positions = [lookup[cell] for cell in membership.cells]
            spatial = (np.asarray(ids)[positions], list(membership.cells),
                       np.asarray(distances)[positions], method)
        counts = {'hourly': {}, 'daily': {}}
        rows = {'hourly': 0, 'daily': 0}
        static = _weather_metadata(archive, bbox, membership, old)
        retained = {f: pq.ParquetFile(output / old['products'][f]['file'])
                    for f in ('hourly', 'daily')} if old else {}
        with (nullcontext(publisher) if publisher is not None else
              TransactionalFamilyPublisher(control)) as publisher:
            paths = {'hourly': publisher.stage_path(result.hourly_path),
                     'daily': publisher.stage_path(result.daily_path)}
            writers = {}
            try:
                for stamp in pd.date_range(first, last, freq='D'):
                    archive.checkpoint()
                    day = stamp.date()
                    reuse = bool(old and old_first <= day <= old_last)
                    day_started = time.perf_counter()
                    action = 'Reusing' if reuse else 'Processing'
                    log(verbosity, f"{action} UTC day {day}")
                    current_parameters = backfill_parameters if reuse else parameters
                    query_started = time.perf_counter()
                    if day == repair_day:
                        current_parameters = sorted(set(current_parameters) | {'tp'})
                    if current_parameters:
                        log(verbosity, f'Query/cache read for {day}: {", ".join(current_parameters)}')
                        if ((allow_incomplete_final_day and day == last) or
                                ('tp' not in current_parameters and
                                 int(pd.Timestamp(day, tz='UTC').timestamp()) + 86400 > archive.times[-1])):
                            cube = archive.cube(day, current_parameters, allow_missing_boundary=True)
                        else:
                            cube = archive.cube(day, current_parameters)
                    elapsed_query = time.perf_counter() - query_started if current_parameters else 0.0
                    query_seconds += elapsed_query
                    processing_started = time.perf_counter()
                    repaired = None
                    if day == repair_day:
                        original = {f: retained[f].read_row_group((day - old_first).days)
                                    for f in ('hourly', 'daily')}
                        fresh = hourly_day(cube, day, {'PRECIPITATION_MM': selected['hourly']['PRECIPITATION_MM']}, spatial, {})
                        from .endpoint_repair import repair_precipitation
                        repaired = repair_precipitation(original, fresh)
                    for frequency in ('hourly', 'daily'):
                        if reuse:
                            table = (repaired[frequency] if repaired else
                                     retained[frequency].read_row_group((day - old_first).days))
                            if backfill[frequency]:
                                added = wide_day(cube, day, backfill[frequency], frequency,
                                                 spatial, counts[frequency])
                                table = _align_backfill(table, added, backfill[frequency])
                            table = table.select([*table.column_names[:2], *selected[frequency]])
                        else:
                            table = wide_day(cube, day, selected[frequency], frequency,
                                             spatial, counts[frequency])
                        metadata = dict(static, frequency=frequency,
                                        columns={k: v['units'] for k, v in selected[frequency].items()})
                        schema_metadata = {b'meteorology': json.dumps(metadata, sort_keys=True).encode()}
                        if membership is not None:
                            schema_metadata[b'coastal_reporting'] = json.dumps(dict(
                                mask_revision=membership.mask_revision,
                                membership_sha256=membership.artifact_sha256,
                                policy='exact approved R5 membership')).encode()
                        table = table.replace_schema_metadata(schema_metadata)
                        if shutil.disk_usage(control).free < table.nbytes * 2 + 64 * 1024**2:
                            raise ValueError('Insufficient disk to stage the regional output.')
                        if frequency not in writers:
                            writers[frequency] = pq.ParquetWriter(paths[frequency], table.schema,
                                                                 compression='zstd')
                        writers[frequency].write_table(table, row_group_size=65536)
                        rows[frequency] += len(table)
                    elapsed_processing = time.perf_counter() - processing_started
                    processing_seconds += elapsed_processing
                    log(verbosity, f'Day {day} complete: query/cache {elapsed_query:.2f}s; '
                        f'processing/write {elapsed_processing:.2f}s; total {time.perf_counter() - day_started:.2f}s')
            finally:
                for writer in writers.values():
                    writer.close()
            if old:
                for frequency in counts:
                    for name, statuses in old['products'][frequency]['value_status_counts'].items():
                        totals = counts[frequency].setdefault(name, {})
                        for label, total in statuses.items():
                            totals[label] = totals.get(label, 0) + total
            if repair_day:
                for frequency in counts:
                    totals = counts[frequency]['PRECIPITATION_MM']
                    cells = len(spatial[1])
                    totals['PARTIAL' if frequency == 'daily' else 'UNAVAILABLE'] -= cells
                    totals['COMPLETE'] += cells
            finalize_started = time.perf_counter()
            log(verbosity, 'Finalizing product checksums, status totals and source evidence')
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
                           products=products, incomplete_final_day=bool(new_partial or
                               (old and not repair_day and last == old_last and old.get('incomplete_final_day', False))),
                           acquisition=dict(requests=archive.requests,
                                                              response_bytes=archive.bytes))
            catalog['source_evidence'] = evidence
            retained_evidence = dict(old.get('source_evidence_by_sha256', {})) if old else {}
            if old and old.get('source_evidence') and old.get('source_evidence_sha256'):
                retained_evidence[old['source_evidence_sha256']] = old['source_evidence']
            catalog['source_evidence_by_sha256'] = {**retained_evidence, evidence_hash: evidence}
            if old and old.get('source_catalogs_by_sha256'):
                catalog['source_catalogs_by_sha256'] = old['source_catalogs_by_sha256']
            catalog['source_payload_retention'] = dict(policy='final-products-only' if owned_cache else 'caller-managed',
                payloads_retained=not owned_cache, rebuild_requires_acquisition=owned_cache)
            catalog['latest_complete_all_metric_day'] = str(
                last - timedelta(days=int(catalog['incomplete_final_day'])))
            catalog['partial_metric_coverage'] = ([dict(date=str(last), metric='PRECIPITATION_MM',
                expected_hours=24, valid_hours=23, value_status='PARTIAL',
                missing_interval_end_utc=str(last + timedelta(days=1)) + 'T00:00:00Z')]
                if new_partial else [])
            if old:
                catalog['partial_metric_coverage'] = [*([] if repair_day else old.get('partial_metric_coverage', [])),
                    *catalog['partial_metric_coverage']]
                previous_epochs = old.get('source_epochs') or [dict(
                    start_date=old['start_date'], end_date=old['end_date'],
                    catalog_sha256=checksum_path(result.catalog_path),
                    metadata_sha256=old['source_metadata_sha256'],
                    evidence_sha256=old['source_evidence_sha256'],
                    source_coverage_end=old['source_coverage_end'],
                    source_method=old.get('source_method', old['method']))]
                old_metrics = {f: [c['name'] for c in old['products'][f]['columns']][2:]
                               for f in selected}
                previous_epochs = [dict(e, metrics=e.get('metrics', old_metrics))
                                   for e in previous_epochs]
                epochs = list(previous_epochs)
                for a, b, fields, scope in queries:
                    if not scope:
                        continue
                    epochs.append(dict(start_date=str(a), end_date=str(b),
                        metadata_sha256=archive.snapshot, evidence_sha256=evidence_hash,
                        source_coverage_end=catalog['source_coverage_end'], source_method=METHOD,
                        metrics={f: list(specs) for f, specs in scope.items()}))
                if old.get('endpoint_repairs'):
                    catalog['endpoint_repairs'] = old['endpoint_repairs']
                if repair_day:
                    catalog['endpoint_repairs'] = [*catalog.get('endpoint_repairs', []), dict(
                        date=str(repair_day), metric='PRECIPITATION_MM',
                        interval_end_utc=str(repair_day + timedelta(days=1)) + 'T00:00:00Z',
                        policy='fill missing final hourly interval only; sum retained 23 hours plus new interval',
                        supersedes_catalog_sha256=checksum_path(result.catalog_path),
                        metadata_sha256=archive.snapshot, evidence_sha256=evidence_hash,
                        source_coverage_end=catalog['source_coverage_end'],
                        cells=len(spatial[1]))]
                catalog['source_epochs'] = sorted(epochs, key=lambda e: e['start_date'])
                catalog['source_coverage_end'] = max([e['source_coverage_end'] for e in epochs] +
                    [e['source_coverage_end'] for e in catalog.get('endpoint_repairs', [])])
                catalog.pop('source_metadata_sha256', None)
                catalog.pop('source_evidence_sha256', None)
            publisher.stage_manifest(result.catalog_path, catalog)
            if owned_cache or membership is not None:
                # Validate the actual staged bytes before any final path is replaced.
                check_weather(publisher.candidate_path(result.catalog_path),
                              _membership=membership, _files=paths)
            finalize_seconds = time.perf_counter() - finalize_started
            log(verbosity, f'Staged files ready after {finalize_seconds:.2f}s finalization; '
                'publishing hourly, daily and catalog together')
            publish_started = time.perf_counter()
            if repair_terminal_precipitation:
                from .endpoint_repair import validate_preserved
                validate_preserved(retained, paths, repair_day, old_first)
            publisher.publish()
            publish_seconds = time.perf_counter() - publish_started
            if owned_cache:
                check_weather(result.catalog_path, _membership=membership)
        if owned_cache:
            try:
                _clear_owned_cache(cache, output)
            except OSError as error:
                raise RuntimeError('Weather product validated; source cleanup incomplete. Retry cleanup.') from error
            log(verbosity, 'Final product readback passed; owned source downloads and arrays removed')
    log(verbosity, f"Published {result.catalog_path}")
    log(verbosity, f'Run timings: query/cache {query_seconds:.2f}s; '
        f'processing/write {processing_seconds:.2f}s; finalization {finalize_seconds:.2f}s; '
        f'publication {publish_seconds:.2f}s; '
        f'pipeline total {time.perf_counter() - started:.2f}s; '
        f'API calls {archive.requests:,}; response bytes {archive.bytes:,}')
    return result


def allowed_variables(variable_list):
    """Reject misspellings and unsupported families before opening a source/cache."""
    if isinstance(variable_list, str):
        raise ValueError('variable_list must be a nonempty list of allowed variable names.')
    values = list(variable_list)
    if not values or any(not isinstance(v, str) or v not in VARIABLES for v in values):
        raise ValueError('Allowed variables: ' + ', '.join(VARIABLES))
    if len(set(values)) != len(values):
        raise ValueError('variable_list contains duplicates.')
    return values


def get_weather(start_date=None, end_date=None, variable_list=None, *, study_config=None,
                output_dir=None, cache_dir=None, workers=4,
                transfer_bytes=8 * 1024**3, max_seconds=3600,
                allow_incomplete_final_day=False, verbosity=None, env_file=None, rebuild=None, bbox=None):
    """Publish approved coastal ERA5 weather for inclusive UTC dates.

    Configure MARINECAST_STUDY_CONFIG once, or pass study_config explicitly.
    All seven allowed families produce the approved 14 daily / 10 hourly fields.
    Reuses pinned geographic chunks; processes one UTC day at a time.
    Defaults from local .env include metrics and rebuild (False preserves existing dates).
    """
    from .marine_reporting import load_reporting_membership
    from .run_settings import resolve_run

    run_started = time.perf_counter()

    settings = resolve_run(start_date, end_date, output_dir, verbosity, env_file,
                           variable_list=variable_list, rebuild=rebuild,
                           study_config=study_config, bbox=bbox)
    start_date, end_date, output_dir, verbosity, variable_list, rebuild = settings.weather_options()
    study_config, bbox, env_file = settings.study_config, settings.bbox, settings.env_file
    variables = allowed_variables(ALLOWED_VARIABLES if variable_list is None else variable_list)
    settings_file = Path(env_file).expanduser().resolve() if env_file is not None else Path.cwd()/'.env'
    log(verbosity, 'Preflight passed. Resolved settings (arguments > environment > .env):')
    for key, value in dict(OUTPUT_DIRECTORY=str(output_dir), START_DATE=start_date, END_DATE=end_date,
                          METRICS=variables, VERBOSITY=verbosity, REBUILD=rebuild).items():
        log(verbosity, f'  {key} = {json.dumps(value)}')
    log(verbosity, 'Settings file: ' + json.dumps(str(settings_file) if settings_file.is_file() else None))
    log(verbosity, f'Mode: {"REBUILD" if rebuild else "APPEND"}. Automatic API retries disabled; '
        'reruns resume verified cached objects.')
    log(verbosity, 'Validating approved study and reporting membership')
    member = select_reporting_membership(load_reporting_membership(study_config), bbox)
    study = member.study_identity['study_config']
    bbox = bbox or study['domain']['bbox_wgs84']
    first, last, _ = validate(start_date, end_date, bbox,
                              workers, transfer_bytes, max_seconds)
    if str(first) < study['time']['start'] or str(last) >= study['time']['end_exclusive']:
        raise ValueError('Dates must lie inside the approved study interval.')
    output = data_path(output_dir if output_dir is not None else Path(member.study_identity['resolved_data_root'])/'Meteorology')
    control = work_root(output)
    existing = None
    # Pin recovery, inspection and publication together so two API writers cannot
    # make append plans against the same stale product.
    with TransactionalFamilyPublisher(control) as publisher:
        if output.exists() and not output.is_dir():
            raise ValueError('Output path must be a dedicated weather directory.')
        if output.exists() and any(output.iterdir()):
            log(verbosity, 'Existing output found; inspecting product files')
            if any(p.is_symlink() for p in output.iterdir()):
                raise ValueError('Weather output files must not be symlinks.')
            expected = {'catalog.json', 'meteorology-hourly.parquet', 'meteorology-daily.parquet'}
            from .companions import is_meteorology_companion
            found = {p.name for p in output.iterdir() if not is_meteorology_companion(p) and not is_temporary_directory(p)}
            if found - expected:
                raise ValueError('Output contains unrelated files; choose a dedicated weather directory.')
            if not rebuild and found:
                if found != expected:
                    raise ValueError('Incomplete weather product; use REBUILD=True to rebuild it.')
                log(verbosity, 'Validating saved product checksums, schema, UTC keys and membership')
                validation_started = time.perf_counter()
                check_weather(output / 'catalog.json', _membership=member)
                log(verbosity, f'Saved product validation passed in {time.perf_counter() - validation_started:.2f}s')
                existing = json.loads((output / 'catalog.json').read_text())
                if (existing.get('bbox') != bbox or
                        existing.get('h3_resolution') != member.resolution):
                    raise ValueError('Existing atmospheric envelope or resolution differs from study.')
                present = {f: {c['name'] for c in existing['products'][f]['columns'][2:]}
                           for f in ('hourly', 'daily')}
                metric_expansion = any(
                    name not in present[f] for f in present
                    for name, spec in metric_specs(f).items() if spec['variable'] in variables)
                retained_families = {spec['variable'] for f in present
                                     for name, spec in metric_specs(f).items() if name in present[f]}
                variables = [v for v in VARIABLES if v in set(variables) | retained_families]
                old_first = date.fromisoformat(existing['start_date'])
                old_last = date.fromisoformat(existing['end_date'])
                if str(old_first) < study['time']['start'] or str(old_last) >= study['time']['end_exclusive']:
                    raise ValueError('Existing dates lie outside the approved study interval.')
                log(verbosity, f'Saved range: {old_first} through {old_last}; '
                    f'new metric backfill: {metric_expansion}')
                if first >= old_first and last <= old_last and not metric_expansion:
                    log(verbosity, 'Requested dates and metrics already present; no acquisition.')
                    log(verbosity, f'Output unchanged: UTC {old_first} through {old_last}; '
                        f'metric families {", ".join(variables)}')
                    log(verbosity, 'Missing dates: none. New metrics: none. '
                        f'Query 0.00s; processing 0.00s; API calls 0; total {time.perf_counter() - run_started:.2f}s')
                    _clean_completed_sources(source_root(output, cache_dir), output)
                    return RegionalResult(output/'meteorology-hourly.parquet',
                                          output/'meteorology-daily.parquet', output/'catalog.json')
                first, last = min(first, old_first), max(last, old_last)
        else:
            log(verbosity, 'No existing data found; building the requested dates and metrics')
        log(verbosity, f'Output plan: UTC {first} through {last}; metric families {", ".join(variables)}')
        if rebuild:
            log(verbosity, 'Rebuild selected: prior weather files stay in place until publication succeeds')
        result = get_data(first, last, bbox=bbox,
                        output_dir=output, cache_dir=source_root(output, cache_dir), workers=workers,
                        transfer_bytes=transfer_bytes, max_seconds=max_seconds,
                        variable_list=variables, membership=member, verbosity=verbosity,
                        existing=existing, publisher=publisher, _discard_cache=True, env_file=env_file,
                        allow_incomplete_final_day=allow_incomplete_final_day)
    log(verbosity, f'Weather run completed in {time.perf_counter() - run_started:.2f}s')
    return result


def check_weather(catalog_path, *, study_config=None, _membership=None, _files=None):
    """Read-only, one-day-at-a-time checks of current or newly generated products.

    Checks exact UTC keys, metric schemas/units, file hashes and null/status totals.
    Pass study_config to additionally verify the approved membership and mask.
    This is structural/source-contract validation, not independent weather truth.
    """
    from .marine_reporting import load_reporting_membership

    path = Path(catalog_path)
    catalog = json.loads(path.read_text())
    if catalog.get('method') not in (METHOD, COASTAL_METHOD) or catalog.get('timezone') != 'UTC':
        raise ValueError('Unsupported weather method or timezone.')
    member = (_membership if _membership is not None else
              load_reporting_membership(study_config) if study_config is not None else None)
    if member is not None and _membership is None:
        member = select_reporting_membership(member, catalog.get('selection_bbox_wgs84'))
    if member is not None and any(catalog.get(k) != v for k, v in selection_metadata(member).items()):
        raise ValueError('Weather regional selection provenance differs from approved membership.')
    if member is not None and any(catalog.get(k) != getattr(member, a) for k, a in (
            ('membership_sha256', 'artifact_sha256'), ('mask_sha256', 'mask_sha256'),
            ('mask_revision', 'mask_revision'))):
        raise ValueError('Catalog differs from approved membership or mask.')
    first = date.fromisoformat(catalog['start_date'])
    last = date.fromisoformat(catalog['end_date'])
    days = (last - first).days + 1
    if days <= 0:
        raise ValueError('Invalid catalog date range.')
    from .lineage import validate_lineage
    validate_lineage(catalog)
    expected_cells = set(member.cells) if member is not None else None
    summary = {}
    for frequency, key, periods, dtype in (
            ('daily', 'DATE', 1, pa.date32()),
            ('hourly', 'DATETIME', 24, pa.timestamp('us', tz='UTC'))):
        product = catalog['products'][frequency]
        if product['file'] != f'meteorology-{frequency}.parquet':
            raise ValueError('Unexpected weather filename.')
        source = _files[frequency] if _files is not None else path.parent / product['file']
        if source.stat().st_size != product['bytes'] or checksum_path(source) != product['sha256']:
            raise ValueError('Weather file size/checksum mismatch.')
        parquet = pq.ParquetFile(source)
        schema = parquet.schema_arrow
        specs = metric_specs(frequency)
        names = schema.names[2:]
        expected_schema = pa.schema([('H3_INDEX', pa.string()), (key, dtype)] +
                                   [(name, pa.float64()) for name in names])
        if (not names or any(name not in specs for name in names) or
                not schema.equals(expected_schema, check_metadata=False) or
                len(set(schema.names)) != len(schema.names)):
            raise ValueError('Unexpected weather Arrow schema.')
        declared = [dict(name=f.name, dtype=str(f.type), units=specs.get(f.name, {}).get('units'))
                    for f in schema]
        if declared != product['columns']:
            raise ValueError('Catalog columns/types/units differ from metric contract.')
        metadata = json.loads((schema.metadata or {}).get(b'meteorology', b'{}'))
        if metadata.get('method') != catalog['method'] or metadata.get('timezone') != 'UTC':
            raise ValueError('Parquet metadata differs from catalog.')
        if catalog['method'] == COASTAL_METHOD:
            coastal = json.loads((schema.metadata or {}).get(b'coastal_reporting', b'{}'))
            if any(coastal.get(k) != catalog[k] for k in ('membership_sha256', 'mask_revision')):
                raise ValueError('Parquet membership differs from catalog.')
        if parquet.num_row_groups != days:
            raise ValueError('Expected one complete UTC day per row group.')
        nulls = dict.fromkeys(names, 0)
        rows = 0
        for i in range(days):
            table = parquet.read_row_group(i)
            frame = table.select(['H3_INDEX', key]).to_pandas()
            cells = set(frame.H3_INDEX)
            if expected_cells is None:
                expected_cells = cells
            day = first + timedelta(days=i)
            stamps = ({day} if periods == 1 else
                      set(pd.date_range(day, periods=24, freq='h', tz='UTC')))
            if (cells != expected_cells or len(frame) != len(cells) * periods or
                    set(frame[key]) != stamps or frame.duplicated().any()):
                raise ValueError('Missing, duplicate or off-grid UTC reporting keys.')
            if catalog.get('reporting_cell_count', len(cells)) != len(cells):
                raise ValueError('Reporting cell count differs from catalog.')
            for name in names:
                values = table[name].to_numpy(zero_copy_only=False)
                if np.isinf(values).any() or int(np.isnan(values).sum()) != table[name].null_count:
                    raise ValueError('Nonfinite weather values must be Arrow nulls.')
                nulls[name] += table[name].null_count
            rows += len(table)
        if rows != product['rows']:
            raise ValueError('Weather row count differs from catalog.')
        for name, missing in nulls.items():
            statuses = product['value_status_counts'][name]
            if (any(type(v) is not int or v < 0 for v in statuses.values()) or
                    sum(statuses.values()) != rows or statuses.get('COMPLETE', 0) != rows - missing):
                raise ValueError('Weather status counts disagree with actual nulls.')
        if 'null_counts' in product and product['null_counts'] != nulls:
            raise ValueError('Weather null counts differ from catalog.')
        summary[frequency] = dict(rows=rows, metrics=len(names), null_counts=nulls)
    return dict(valid=True, method=catalog['method'], start_date=str(first), end_date=str(last),
                reporting_cell_count=len(expected_cells), membership_verified=member is not None,
                products=summary)


def main():
    """Small date-range CLI for the same API and read-only checks."""
    import argparse

    parser = argparse.ArgumentParser(description='Coastal Weather, Solar Cycle and Solar Radiation')
    commands = parser.add_subparsers(dest='command', required=True)
    get = commands.add_parser('get')
    get.add_argument('start_date', nargs='?')
    get.add_argument('end_date', nargs='?')
    get.add_argument('--variables', nargs='+', choices=VARIABLES)
    get.add_argument('--study-config')
    get.add_argument('--output-dir')
    get.add_argument('--env-file', help='Run defaults file (default: .env in current directory)')
    get.add_argument('--verbose', dest='verbosity', action=argparse.BooleanOptionalAction, default=None)
    get.add_argument('--rebuild', action=argparse.BooleanOptionalAction, default=None,
                     help='Replace requested range; --no-rebuild preserves and extends existing dates')
    get.add_argument('--cache-dir')
    get.add_argument('--allow-incomplete-final-day', action='store_true')
    check = commands.add_parser('check')
    check.add_argument('catalog_path')
    check.add_argument('--study-config')
    pre = commands.add_parser('preflight', help='Validate configuration without running')
    pre.add_argument('--env-file')
    solar = commands.add_parser('solar', help='Build the distinct calculated Solar Cycle companion')
    solar.add_argument('start_date', nargs='?')
    solar.add_argument('end_date', nargs='?')
    solar.add_argument('--study-config')
    solar.add_argument('--output-dir')
    solar.add_argument('--env-file')
    solar.add_argument('--rebuild', action=argparse.BooleanOptionalAction, default=None)
    solar.add_argument('--verbose', dest='verbosity', action=argparse.BooleanOptionalAction, default=None)
    solar_check = commands.add_parser('solar-check', help='Read-only Solar Cycle validation')
    solar_check.add_argument('catalog_path')
    solar_check.add_argument('--study-config')
    radiation = commands.add_parser('radiation', help='Build or append the separate Solar Radiation product')
    radiation.add_argument('start_date', nargs='?')
    radiation.add_argument('end_date', nargs='?')
    radiation.add_argument('--study-config')
    radiation.add_argument('--output-dir')
    radiation.add_argument('--cache-dir')
    radiation.add_argument('--env-file')
    radiation.add_argument('--rebuild', action=argparse.BooleanOptionalAction, default=None)
    radiation.add_argument('--allow-incomplete-final-day', action='store_true')
    radiation.add_argument('--verbose', dest='verbosity', action=argparse.BooleanOptionalAction, default=None)
    radiation_check = commands.add_parser('radiation-check', help='Read-only Solar Radiation validation')
    radiation_check.add_argument('catalog_path')
    radiation_check.add_argument('--study-config')
    options = vars(parser.parse_args())
    command = options.pop('command')
    if command == 'radiation':
        from .radiation import get_solar_radiation
        print(get_solar_radiation(**options).catalog_path)
    elif command == 'radiation-check':
        from .radiation import check_solar_radiation
        print(json.dumps(check_solar_radiation(**options), indent=2))
    elif command == 'solar':
        from .solar import get_solar_cycle
        print(get_solar_cycle(**options).catalog_path)
    elif command == 'solar-check':
        from .solar import check_solar_cycle
        print(json.dumps(check_solar_cycle(**options), indent=2))
    elif command == 'preflight':
        from .run_settings import resolve_run
        settings = resolve_run(**options)
        print(json.dumps(dict(START_DATE=settings.start_date, END_DATE=settings.end_date,
            OUTPUT_DIRECTORY=str(settings.output_dir) if settings.output_dir is not None else None, REBUILD=settings.rebuild, METRICS=settings.metrics,
            VERBOSITY=settings.verbosity, BBOX=settings.bbox,
            MARINECAST_STUDY_CONFIG=str(settings.study_config) if settings.study_config else None), indent=2))
    elif command == 'check':
        print(json.dumps(check_weather(**options), indent=2))
    else:
        options['variable_list'] = options.pop('variables')
        from .run_settings import preflight
        preflight(**{key: options[key] for key in (
            'start_date', 'end_date', 'output_dir', 'verbosity', 'env_file', 'variable_list', 'rebuild')})
        print(get_weather(**options).catalog_path)


if __name__ == '__main__':
    main()
