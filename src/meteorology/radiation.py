"""Separate, source-qualified coastal ERA5 Solar Radiation delivery.

Source SSRD and FDIR are preceding-hour energy on a horizontal plane. Public
hourly keys label interval start; daily totals require all 24 intervals. Source
zeros and finite signed values are preserved; unavailable support stays null.
"""
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, timedelta
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
from .compute import mapping
from .core.artifacts import TransactionalFamilyPublisher, atomic_write_json
from .marine_reporting import load_reporting_membership, select_reporting_membership, selection_metadata
from .regional_source import Archive, URL, utcnow
from .run_settings import log, resolve_run
from .storage import data_path, temporary_root

PRODUCT = 'solar-radiation'
METHOD = 'ecmwf-arco-horizontal-radiation-h3-utc-v1'
PARAMETERS = {'ssrd': 'J m**-2', 'fdir': 'J m**-2'}
BASES = {'ssrd': 'DOWNWARD_SHORTWAVE', 'fdir': 'DIRECT_SOLAR'}
OWNER = 'meteorology-solar-radiation-sources-v1'
SOURCES = ['https://confluence.ecmwf.int/pages/viewpage.action?pageId=505390919',
           'https://confluence.ecmwf.int/pages/viewpage.action?pageId=76414402',
           'https://codes.ecmwf.int/grib/param-db/169',
           'https://codes.ecmwf.int/grib/param-db/228021']


@dataclass(frozen=True)
class RadiationResult:
    hourly_path: Path
    daily_path: Path
    catalog_path: Path


def metric_specs(frequency):
    """Explicit quantities: energy is retained; flux is a period mean."""
    specs = {}
    for parameter, base in BASES.items():
        specs[base + '_ENERGY_J_M2'] = dict(parameter=parameter, units='J m-2',
            quantity='energy', aggregate='hour_total' if frequency == 'hourly' else 'day_total')
        name = base + '_IRRADIANCE_W_M2' + ('_MEAN' if frequency == 'daily' else '')
        specs[name] = dict(parameter=parameter, units='W m-2', quantity='irradiance',
                          aggregate='hour_mean' if frequency == 'hourly' else 'day_mean')
    return specs


def _schema(frequency):
    return pa.schema([('H3_INDEX', pa.string()),
        ('DATETIME', pa.timestamp('us', tz='UTC')) if frequency == 'hourly' else ('DATE', pa.date32()),
        *[(name, pa.float64()) for name in metric_specs(frequency)]])


def _conventions():
    return dict(product=PRODUCT, method=METHOD, timezone='UTC', h3_resolution=5,
        source=URL, surface='horizontal plane; downward positive; all-sky',
        direct_definition='FDIR direct horizontal component, not direct-normal irradiance',
        hourly_time='DATETIME is UTC interval start; value covers [DATETIME,DATETIME+1h)',
        source_time='preceding-hour accumulation stamped at interval end; no cumulative differencing',
        daily_time='DATE is [00:00,next 00:00) UTC; source interval ends 01:00 through next midnight',
        null_policy='24 finite intervals required for daily values; missing is not zero; no clipping',
        sampling='nearest verified native 0.25-degree grid point to approved H3 R5 centre',
        effective_resolution='approximately 31 km; H3 adds no independent atmospheric detail',
        metric_specs={f: metric_specs(f) for f in ('hourly', 'daily')})


def _source_contract(archive):
    for p, pid in (('ssrd', 169), ('fdir', 228021)):
        attrs = archive.meta[p + '/.zattrs']
        if (attrs.get('GRIB_paramId') != pid or attrs.get('GRIB_stepType') != 'accum'
                or attrs.get('units') != PARAMETERS[p]):
            raise ValueError('Unsupported radiation source identity, units or accumulation type: ' + p)


def _spatial(archive, member, cache):
    bbox = getattr(member, 'selection_bbox_wgs84', None) or member.study_identity['study_config']['domain']['bbox_wgs84']
    grid = dict(coordinates=archive.coords,
                metadata=dict(grid_hash=hashlib.sha256(archive.coords.tobytes()).hexdigest()))
    indices, cells, distances, method = mapping(grid, bbox, cache/'mapping')
    lookup = {cell: i for i, cell in enumerate(cells)}
    if not set(member.cells) <= lookup.keys():
        raise ValueError('Approved radiation reporting cells extend outside source support.')
    positions = [lookup[c] for c in member.cells]
    return np.asarray(indices)[positions], np.asarray(distances)[positions], method


def _day_tables(archive, day, cells, indices, allow_partial):
    cube = archive.cube(day, list(PARAMETERS), allow_missing_boundary=allow_partial)
    # Explicitly omit source 00:00 and include next 00:00. Accumulations already
    # represent single hours; differencing would corrupt night and daytime energy.
    energy = np.take(cube['values'][:, 1:25], indices, axis=2)
    stamps = pd.date_range(day, periods=24, freq='h', tz='UTC')
    hourly = dict(H3_INDEX=list(cells)*24, DATETIME=stamps.repeat(len(cells)))
    daily = dict(H3_INDEX=cells, DATE=[day]*len(cells))
    for i, base in enumerate(BASES.values()):
        values = energy[i]
        finite = np.isfinite(values)
        total = np.where(finite, values, 0).sum(axis=0)
        total = np.where(finite.sum(axis=0) == 24, total, np.nan)
        hourly[base+'_ENERGY_J_M2'] = pa.array(values.ravel(), mask=~finite.ravel())
        hourly[base+'_IRRADIANCE_W_M2'] = pa.array((values/3600).ravel(), mask=~finite.ravel())
        daily[base+'_ENERGY_J_M2'] = pa.array(total, from_pandas=True)
        daily[base+'_IRRADIANCE_W_M2_MEAN'] = pa.array(total/86400, from_pandas=True)
    return dict(hourly=pa.Table.from_pydict(hourly, schema=_schema('hourly')),
                daily=pa.Table.from_pydict(daily, schema=_schema('daily')))


def _directory(path):
    expected = {'catalog.json', 'solar-radiation-hourly.parquet', 'solar-radiation-daily.parquet'}
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError('Radiation output must be a dedicated regular directory.')
    if path.exists() and any(p.name not in expected or p.is_symlink() or not p.is_file()
                             for p in path.iterdir()):
        raise ValueError('Radiation output contains unrelated entries; preserved.')


def _cache_owner(cache, output):
    expected = dict(owner=OWNER, destination=str(output))
    marker = cache/'owner.json'
    if cache.is_symlink():
        raise ValueError('Symlinked radiation source cache; preserved.')
    entries = {p.name for p in cache.iterdir()} if cache.exists() else set()
    if entries - {'.publication.lock'}:
        if not marker.is_file() or marker.is_symlink() or json.loads(marker.read_text()) != expected:
            raise ValueError('Radiation source-cache ownership mismatch; preserved.')
        if any(p.name not in {'owner.json', 'objects', 'arrays', 'mapping', '.publication.lock'} or p.is_symlink()
               for p in cache.iterdir()):
            raise ValueError('Unowned radiation cache entries; preserved.')
    else:
        cache.mkdir(parents=True, exist_ok=True)
        atomic_write_json(marker, expected, overwrite=False)


def _clean_cache(cache, output):
    _cache_owner(cache, output)
    for name in ('objects', 'arrays', 'mapping'):
        child = cache/name
        if child.exists():
            shutil.rmtree(child)


def _native_samples(archive, paths, first, acquired, cells, indices):
    """Compare written energies directly with native samples, not another reducer."""
    days = sorted({d for a, b in acquired for d in (a, b)} |
                  {date(2012, 2, 29) for a, b in acquired if a <= date(2012, 2, 29) <= b})
    files = {f: pq.ParquetFile(p) for f, p in paths.items()}
    evidence = []
    for day in days:
        cube = archive.cube(day, list(PARAMETERS), allow_missing_boundary=True)
        written = {f: q.read_row_group((day-first).days) for f, q in files.items()}
        for i, base in enumerate(BASES.values()):
            raw = np.take(cube['values'][i, 1:25], indices, axis=1)
            actual = written['hourly'][base+'_ENERGY_J_M2'].to_numpy().reshape(24, len(cells))
            expected = np.where(np.isfinite(raw), raw, np.nan)
            if not np.array_equal(actual, expected, equal_nan=True):
                raise ValueError('Published radiation differs from native interval-end samples.')
        evidence.append(dict(date=str(day), cells=len(cells), hourly_energy_exact_native_parity=True))
    return evidence


def get_solar_radiation(start_date=None, end_date=None, *, study_config=None,
        output_dir=None, cache_dir=None, workers=4, transfer_bytes=8*1024**3,
        max_seconds=3600, allow_incomplete_final_day=False, rebuild=None, verbosity=None,
        env_file=None, bbox=None):
    """Publish the distinct SSRD/FDIR product, preserving retained vintages on append.

    Omitted end discovers actual source coordinates and selects complete UTC days.
    Explicit opt-in can include a terminal day lacking only its next-midnight hour.
    Append retains old values, including nulls; rebuild explicitly replaces them.
    """
    settings = resolve_run(start_date, end_date, output_dir, verbosity, env_file,
                           rebuild=rebuild, study_config=study_config, bbox=bbox,
                           require_dates=False, product=PRODUCT)
    start_date, end_date, output_dir, verbosity, _, rebuild = settings.weather_options()
    study_config, bbox, env_file = settings.study_config, settings.bbox, settings.env_file
    for value in (allow_incomplete_final_day, rebuild, verbosity):
        if type(value) is not bool:
            raise ValueError('Radiation mode flags must be booleans.')
    if (type(workers) is not int or not 1 <= workers <= 4 or type(transfer_bytes) is not int
            or transfer_bytes <= 0 or not np.isfinite(max_seconds) or not 0 < max_seconds <= 86400):
        raise ValueError('Radiation requires 1..4 workers and positive bounded transfer/time limits.')
    supplied = [date.fromisoformat(str(d)) if d is not None else None for d in (start_date, end_date)]
    if all(supplied) and supplied[0] > supplied[1]:
        raise ValueError('Radiation start_date must be on or before end_date.')
    base_member = load_reporting_membership(study_config)
    member = select_reporting_membership(base_member, bbox)
    study = member.study_identity['study_config']
    bbox = bbox or study['domain']['bbox_wgs84']
    first = supplied[0] or date.fromisoformat(study['time']['start'])
    limit = date.fromisoformat(study['time']['end_exclusive'])-timedelta(days=1)
    if (str(first) < study['time']['start'] or first > limit
            or (supplied[1] and (supplied[1] > limit or supplied[1] < first))):
        raise ValueError('Radiation dates must lie inside the approved study interval.')
    data_root = Path(member.study_identity['resolved_data_root'])
    raw_output = Path(output_dir).expanduser().absolute() if output_dir is not None else data_root/'Meteorology'/PRODUCT
    if any(p.is_symlink() for p in (raw_output, *raw_output.parents)):
        raise ValueError('Radiation output ancestors must not be symlinks.')
    output = data_path(raw_output)
    _directory(output)
    token = hashlib.sha256(str(output).encode()).hexdigest()[:16]
    control = temporary_root(output, PRODUCT)
    cache = (Path(cache_dir).expanduser().absolute()/('radiation-'+token)
             if cache_dir is not None else control/'downloads')
    if cache.is_symlink() or output == cache.resolve() or output in cache.resolve().parents or cache.resolve() in output.parents:
        raise ValueError('Radiation source cache and output must be separate.')
    cache = data_path(cache)
    if output.is_relative_to(control) or control.is_relative_to(output):
        raise ValueError('Radiation delivery and control directories must be separate.')
    result = RadiationResult(output/'solar-radiation-hourly.parquet',
                             output/'solar-radiation-daily.parquet', output/'catalog.json')
    code = {name: checksum_path(Path(__file__).parent/name) for name in
            ('radiation.py', 'regional_source.py', 'compute.py')}
    with TransactionalFamilyPublisher(control) as publisher:
        _directory(output)
        old = None
        if output.exists() and any(output.iterdir()):
            old = json.loads(result.catalog_path.read_text())
            old_member = select_reporting_membership(base_member, old.get('selection_bbox_wgs84'))
            check_solar_radiation(result.catalog_path, _member=old_member if rebuild else member)
            if not rebuild and old['code_sha256'] != code:
                raise ValueError('Radiation code changed; explicit rebuild=True is required.')
            if (not rebuild and supplied[1] and old['start_date'] <= str(first)
                    and old['end_date'] >= str(supplied[1])):
                if cache.exists():
                    with TransactionalFamilyPublisher._exclusive_parent(cache):
                        _clean_cache(cache, output)
                log(verbosity, 'Radiation request already covered; no acquisition or publication.')
                return result
        # Bound staging before source access; acquisition also has per-object and
        # per-array checks. Peak includes two native fields, source objects and output.
        estimate = (limit-first).days*len(member.cells)*24*64 + 4*1024**3
        if shutil.disk_usage(control).free < estimate + 512*1024**2:
            raise ValueError('Insufficient disk headroom for radiation source and publication staging.')
        atomic_write_json(control/'staging-manifest.json', dict(owner=OWNER, destination=str(output),
            exact_paths=[str(publisher.staging), str(cache)], expected_peak_bytes=estimate,
            retained_inputs='approved registry and mask', retained_output=str(output),
            cleanup_status='source payloads retained until full final readback; failed runs resumable'), overwrite=True)
        with TransactionalFamilyPublisher._exclusive_parent(cache):
            _cache_owner(cache, output)
            archive = Archive(cache, bbox, workers=workers,
                transfer_bytes=transfer_bytes, max_seconds=max_seconds, parameter_units=PARAMETERS,
                verbosity=verbosity, env_file=env_file)
            _source_contract(archive)
            frontier = pd.Timestamp(archive.times[-1], unit='s', tz='UTC')
            complete_day = frontier.floor('D').date()-timedelta(days=1)
            latest_day = (frontier.date() if allow_incomplete_final_day and frontier.hour == 23 else complete_day)
            last = supplied[1] or min(latest_day, limit)
            boundary = pd.Timestamp(last+timedelta(days=1), tz='UTC').timestamp()
            partial = boundary > archive.times[-1]
            if (first > last or pd.Timestamp(first, tz='UTC').timestamp()+3600 < archive.times[0]
                    or (partial and (not allow_incomplete_final_day or boundary != archive.times[-1]+3600))):
                raise ValueError('Radiation dates exceed source support; only one opted-in terminal hour may be missing.')
            log(verbosity, f'Radiation source ends {frontier.isoformat()}; complete UTC day {complete_day}; '
                f'requested output {first} through {last}; partial final day {partial}')
            if old and not rebuild and old['start_date'] <= str(first) and old['end_date'] >= str(last):
                archive.blocks.clear()
                _clean_cache(cache, output)
                return result
            retained = old if old and not rebuild else None
            old_first = date.fromisoformat(retained['start_date']) if retained else None
            old_last = date.fromisoformat(retained['end_date']) if retained else None
            if retained:
                if old_first < date.fromisoformat(study['time']['start']) or old_last > limit:
                    raise ValueError('Retained radiation dates exceed approved study support.')
                first, last = min(first, old_first), max(last, old_last)
            acquired = ([(first, last)] if not retained else
                [(a, b) for a, b in ((first, old_first-timedelta(days=1)),
                                    (old_last+timedelta(days=1), last)) if a <= b])
            indices, distances, spatial_method = _spatial(archive, member, cache)
            meta = dict(_conventions(), reporting_cell_count=len(member.cells),
                membership_sha256=member.artifact_sha256, mask_sha256=member.mask_sha256,
                mask_revision=member.mask_revision, **selection_metadata(member))
            paths = {f: publisher.stage_path(getattr(result, f+'_path')) for f in ('hourly', 'daily')}
            saved = {f: pq.ParquetFile(output/retained['products'][f]['file']) for f in paths} if retained else {}
            rows = dict.fromkeys(paths, 0)
            nulls = {f: dict.fromkeys(metric_specs(f), 0) for f in paths}
            days = (last-first).days+1
            with ExitStack() as stack:
                writers = {f: stack.enter_context(pq.ParquetWriter(p, _schema(f).with_metadata(
                    {b'solar_radiation': json.dumps(meta, sort_keys=True).encode()}), compression='zstd')) for f, p in paths.items()}
                for i in range(days):
                    archive.checkpoint()
                    day = first+timedelta(days=i)
                    reuse = bool(retained and old_first <= day <= old_last)
                    if i % 365 == 0:
                        log(verbosity, f'Radiation {i+1:,}/{days:,} UTC days; {"retain" if reuse else "native source"}')
                        if shutil.disk_usage(control).free < 512*1024**2:
                            raise ValueError('Radiation staging stopped before exhausting disk space.')
                    tables = ({f: q.read_row_group((day-old_first).days) for f, q in saved.items()} if reuse
                              else _day_tables(archive, day, member.cells, indices, allow_incomplete_final_day and day == last))
                    for f, table in tables.items():
                        writers[f].write_table(table.replace_schema_metadata({b'solar_radiation': json.dumps(meta, sort_keys=True).encode()}))
                        rows[f] += len(table)
                        for name in nulls[f]:
                            nulls[f][name] += table[name].null_count
            native = _native_samples(archive, paths, first, acquired, member.cells, indices)
            evidence = dict(metadata_sha256=archive.snapshot, objects=archive.used,
                fields={p: archive.meta[p+'/.zattrs'] for p in PARAMETERS}, references=SOURCES)
            evidence_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
            all_evidence = dict(retained.get('source_evidence_by_sha256', {})) if retained else {}
            all_evidence[evidence_hash] = evidence
            epochs = list(retained['source_epochs']) if retained else []
            epochs += [dict(start_date=str(a), end_date=str(b), source_coverage_end=frontier.isoformat(),
                evidence_sha256=evidence_hash, metadata_sha256=archive.snapshot) for a, b in acquired]
            catalog = dict(meta, schema_version=1, display_name='Solar Radiation',
                start_date=str(first), end_date=str(last), run_date_utc=utcnow(),
                requested_start_date=str(supplied[0] or date.fromisoformat(study['time']['start'])),
                requested_end_date=str(supplied[1] or limit), source_coverage_end=frontier.isoformat(),
                latest_complete_source_day=str(complete_day), study_identity=member.study_identity,
                software_version=__version__, code_sha256=code,
                environment=dict(python=platform.python_version(), numpy=np.__version__,
                    pandas=pd.__version__, pyarrow=pa.__version__, h3=h3.__version__),
                availability=dict(kind='retrospective_reanalysis', historical_available_at_utc=None,
                                  forecast_eligibility='not established'),
                source_revision='unknown; final ERA5 and preliminary ERA5T not distinguished',
                source_forecast_init_lead='unavailable in this ARCO representation',
                attribution='Copernicus Climate Change Service / ECMWF ERA5, CC BY 4.0',
                source_epochs=sorted(epochs, key=lambda x: x['start_date']),
                source_evidence_by_sha256=all_evidence, native_sample_checks=native,
                acquisition=dict(requests=archive.requests, response_bytes=archive.bytes),
                sampling_crosswalk=dict(method=spatial_method, native_grid_sha256=hashlib.sha256(archive.coords.tobytes()).hexdigest(),
                    native_points_sampled=len(set(indices.tolist())), cells=[dict(h3_index=c,
                        source_latitude=float(archive.coords[j, 0]), source_longitude=float(archive.coords[j, 1]),
                        distance_m=float(d)) for c, j, d in zip(member.cells, indices, distances)]),
                products={f: dict(file=getattr(result, f+'_path').name, rows=rows[f],
                    bytes=p.stat().st_size, sha256=checksum_path(p), null_counts=nulls[f],
                    columns=[dict(name=field.name, dtype=str(field.type), units=metric_specs(f).get(field.name, {}).get('units'))
                             for field in _schema(f)]) for f, p in paths.items()})
            candidate = publisher.stage_manifest(result.catalog_path, catalog)
            report = check_solar_radiation(candidate, _member=member, _files=paths)
            catalog['quality'] = report['quality']
            atomic_write_json(candidate, catalog, overwrite=True)
            log(verbosity, 'Radiation staged keys, hashes and every hourly/daily energy reduction passed; publishing')
            publisher.publish()
            check_solar_radiation(result.catalog_path, _member=member)
            archive.blocks.clear()
            _clean_cache(cache, output)
            atomic_write_json(control/'staging-manifest.json', dict(owner=OWNER, destination=str(output),
                products=catalog['products'], cleanup_status='committed; final readback passed; owned source payloads removed'), overwrite=True)
    return result


def check_solar_radiation(catalog_path, *, study_config=None, _member=None, _files=None):
    """Verify full UTC lattice, lineage, nulls, units and all energy/flux reductions."""
    path = Path(catalog_path)
    catalog = json.loads(path.read_text())
    if catalog.get('schema_version') != 1 or any(catalog.get(k) != v for k, v in _conventions().items()):
        raise ValueError('Unsupported Solar Radiation catalog or quantity conventions.')
    member = _member or (load_reporting_membership(study_config) if study_config is not None else None)
    if member is not None and _member is None:
        member = select_reporting_membership(member, catalog.get('selection_bbox_wgs84'))
    if member is not None and any(catalog.get(k) != v for k, v in selection_metadata(member).items()):
        raise ValueError('Regional selection provenance differs from approved membership.')
    if member is not None and any(catalog.get(k) != v for k, v in dict(
            membership_sha256=member.artifact_sha256, mask_sha256=member.mask_sha256,
            mask_revision=member.mask_revision, reporting_cell_count=len(member.cells)).items()):
        raise ValueError('Radiation catalog differs from approved membership or mask.')
    first, last = [date.fromisoformat(catalog[k]) for k in ('start_date', 'end_date')]
    days = (last-first).days+1
    if days <= 0:
        raise ValueError('Invalid radiation date interval.')
    cursor = first
    for epoch in sorted(catalog['source_epochs'], key=lambda x: x['start_date']):
        if date.fromisoformat(epoch['start_date']) != cursor:
            raise ValueError('Radiation source epochs must partition retained dates exactly.')
        evidence = catalog['source_evidence_by_sha256'][epoch['evidence_sha256']]
        if (hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest() != epoch['evidence_sha256']
                or evidence['metadata_sha256'] != epoch['metadata_sha256']):
            raise ValueError('Radiation source evidence hash mismatch.')
        cursor = date.fromisoformat(epoch['end_date'])+timedelta(days=1)
    if cursor != last+timedelta(days=1):
        raise ValueError('Radiation source epochs omit output dates.')
    files, nulls, totals = {}, {}, {}
    for f in ('hourly', 'daily'):
        product = catalog['products'][f]
        if product['file'] != f'solar-radiation-{f}.parquet':
            raise ValueError('Unexpected radiation filename.')
        source = _files[f] if _files is not None else path.parent/product['file']
        if source.is_symlink() or source.stat().st_size != product['bytes'] or checksum_path(source) != product['sha256']:
            raise ValueError('Radiation file size/checksum mismatch.')
        q = pq.ParquetFile(source)
        metadata = json.loads((q.schema_arrow.metadata or {}).get(b'solar_radiation', b'{}'))
        columns = [dict(name=field.name, dtype=str(field.type), units=metric_specs(f).get(field.name, {}).get('units')) for field in _schema(f)]
        if (not q.schema_arrow.equals(_schema(f), check_metadata=False) or q.num_row_groups != days
                or metadata != {k: catalog[k] for k in metadata} or metadata.get('method') != METHOD
                or product['columns'] != columns):
            raise ValueError('Radiation schema, units, metadata or row groups differ.')
        files[f] = q
        nulls[f] = dict.fromkeys(metric_specs(f), 0)
        totals[f] = 0
    cells = tuple(member.cells) if member is not None else tuple(files['daily'].read_row_group(0, columns=['H3_INDEX'])['H3_INDEX'].to_pylist())
    if (not cells or cells != tuple(sorted(set(cells))) or len(cells) != catalog['reporting_cell_count']
            or any(not h3.is_valid_cell(c) or h3.get_resolution(c) != 5 for c in cells)
            or hashlib.sha256(('\n'.join(cells)+'\n').encode()).hexdigest() != catalog['membership_sha256']):
        raise ValueError('Invalid radiation reporting-cell identity.')
    ids = {f: pa.array(list(cells)*(24 if f == 'hourly' else 1)) for f in files}
    quality = dict(partial_days=[], negative_hourly_energy={p: 0 for p in PARAMETERS},
                   direct_above_downward_hourly_count=0)
    for i in range(days):
        day = first+timedelta(days=i)
        tables = {f: q.read_row_group(i) for f, q in files.items()}
        for f, table in tables.items():
            key = 'DATETIME' if f == 'hourly' else 'DATE'
            stamps = (pa.array(pd.date_range(day, periods=24, freq='h', tz='UTC').repeat(len(cells)),
                               type=pa.timestamp('us', tz='UTC')) if f == 'hourly'
                      else pa.array([day]*len(cells), type=pa.date32()))
            if not table['H3_INDEX'].combine_chunks().equals(ids[f]) or not table[key].combine_chunks().equals(stamps):
                raise ValueError('Missing, duplicate, reordered or off-grid radiation UTC keys.')
            totals[f] += len(table)
            for name in nulls[f]:
                values = table[name].to_numpy()
                if np.isinf(values).any() or np.isnan(values).sum() != table[name].null_count:
                    raise ValueError('Nonfinite radiation values must be Arrow nulls.')
                nulls[f][name] += table[name].null_count
        values_by_parameter = {}
        for parameter, base in BASES.items():
            hourly = tables['hourly'][base+'_ENERGY_J_M2'].to_numpy().reshape(24, len(cells))
            values_by_parameter[parameter] = hourly
            counts = np.isfinite(hourly).sum(axis=0)
            expected = np.where(counts == 24, np.where(np.isfinite(hourly), hourly, 0).sum(axis=0), np.nan)
            expected_values = {'hourly': {base+'_IRRADIANCE_W_M2': hourly.ravel()/3600},
                'daily': {base+'_ENERGY_J_M2': expected, base+'_IRRADIANCE_W_M2_MEAN': expected/86400}}
            for f, checks in expected_values.items():
                for name, expected_value in checks.items():
                    if not np.allclose(tables[f][name].to_numpy(), expected_value,
                                       rtol=1e-12, atol=1e-8, equal_nan=True):
                        raise ValueError('Radiation energy/irradiance or complete-day reduction mismatch.')
            quality['negative_hourly_energy'][parameter] += int((hourly < 0).sum())
            if (counts < 24).any():
                quality['partial_days'].append(dict(date=str(day), parameter=parameter,
                    affected_cells=int((counts < 24).sum()), minimum_valid_hours=int(counts.min()),
                    maximum_valid_hours=int(counts.max())))
        quality['direct_above_downward_hourly_count'] += int((values_by_parameter['fdir'] > values_by_parameter['ssrd']+1e-6).sum())
    for f in files:
        if nulls[f] != catalog['products'][f]['null_counts'] or totals[f] != catalog['products'][f]['rows']:
            raise ValueError('Radiation row/null accounting mismatch.')
    if 'quality' in catalog and catalog['quality'] != quality:
        raise ValueError('Radiation quality summary differs from actual values.')
    return dict(valid=True, membership_verified=member is not None, start_date=str(first), end_date=str(last),
                reporting_cell_count=len(cells), products={f: dict(rows=totals[f], null_counts=nulls[f]) for f in files}, quality=quality)
