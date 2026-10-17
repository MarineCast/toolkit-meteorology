"""Publish approved coastal reporting cells from retained wide ERA5 products.

No acquisition: source products must cover an adjacent, nonoverlapping UTC range.
Run with ``python -m meteorology.coastal --help``. Source vintages stay explicit.
"""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import time

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .artifacts import checksum_path
from .core.artifacts import TransactionalFamilyPublisher
from .marine_reporting import load_reporting_membership
from .regional_source import utcnow


def publish_coastal(sources, study_config, output_dir, control_dir):
    """Stream exact membership selection, preserving all metric values and UTC keys."""
    started = time.monotonic()
    membership = load_reporting_membership(study_config)
    catalogs = [(Path(p).resolve(), json.loads((Path(p) / 'catalog.json').read_text()))
                for p in sources]
    catalogs.sort(key=lambda item: item[1]['start_date'])
    first = catalogs[0][1]
    previous = None
    for root, catalog in catalogs:
        if catalog['h3_resolution'] != membership.resolution:
            raise ValueError('Retained product resolution differs from approved membership.')
        if catalog['bbox'] != membership.study_identity['study_config']['domain']['bbox_wgs84']:
            raise ValueError('Retained atmospheric envelope differs from study.')
        if previous is not None and date.fromisoformat(catalog['start_date']) != previous + timedelta(days=1):
            raise ValueError('Sources must be contiguous and nonoverlapping.')
        previous = date.fromisoformat(catalog['end_date'])
        for frequency in ('hourly', 'daily'):
            product = catalog['products'][frequency]
            if product['columns'] != first['products'][frequency]['columns']:
                raise ValueError('Source metric schemas differ.')
            if checksum_path(root / product['file']) != product['sha256']:
                raise ValueError('Retained source product checksum mismatch.')
    output, control = Path(output_dir).resolve(), Path(control_dir).resolve()
    if output in [p for p, _ in catalogs]:
        raise ValueError('Retained source must be preserved outside destination.')
    selected = pa.array(membership.cells)
    products = {}
    with TransactionalFamilyPublisher(control) as publisher:
        for frequency, key, expected_per_day in (('daily', 'DATE', 1), ('hourly', 'DATETIME', 24)):
            target = output / f'meteorology-{frequency}.parquet'
            stage = publisher.stage_path(target)
            writer = None
            rows = 0
            nulls = {}
            try:
                for root, catalog in catalogs:
                    source = pq.ParquetFile(root / catalog['products'][frequency]['file'])
                    expected = date.fromisoformat(catalog['start_date'])
                    for index in range(source.num_row_groups):
                        table = source.read_row_group(index)
                        table = table.filter(pc.is_in(table['H3_INDEX'], value_set=selected))
                        # Default producer commits exactly one UTC day per row group.
                        stamps = table[key].to_pylist()
                        days = {s if frequency == 'daily' else s.date() for s in stamps}
                        if days != {expected} or len(table) != len(selected) * expected_per_day:
                            raise ValueError('Incomplete daily membership/time support.')
                        cells = table['H3_INDEX'].to_pylist()
                        pairs = set(zip(cells, stamps))
                        if len(pairs) != len(table) or set(cells) != set(membership.cells):
                            raise ValueError('Duplicate or missing reporting keys.')
                        if frequency == 'hourly' and {s.hour for s in stamps} != set(range(24)):
                            raise ValueError('Incomplete UTC hourly support.')
                        metadata = {b'meteorology': json.dumps(dict(
                            method='era5-retained-exact-coastal-r5-v1', timezone='UTC',
                            sampling='nearest native ERA5 quarter-degree point',
                            provenance='catalog source_epochs identifies each date vintage')).encode()}
                        metadata[b'coastal_reporting'] = json.dumps(dict(
                            mask_revision=membership.mask_revision,
                            membership_sha256=membership.artifact_sha256,
                            policy='exact approved R5 membership; source values unchanged')).encode()
                        table = table.replace_schema_metadata(metadata)
                        if writer is None:
                            writer = pq.ParquetWriter(stage, table.schema, compression='zstd')
                        writer.write_table(table)
                        rows += len(table)
                        for name in table.column_names:
                            if name not in ('H3_INDEX', key):
                                nulls[name] = nulls.get(name, 0) + table[name].null_count
                        expected += timedelta(days=1)
                    if expected != date.fromisoformat(catalog['end_date']) + timedelta(days=1):
                        raise ValueError('Source date range does not match its catalog.')
            finally:
                if writer:
                    writer.close()
            statuses = {}
            for name, count in nulls.items():
                reasons = {label for _, c in catalogs
                           for label, total in c['products'][frequency].get(
                               'value_status_counts', {}).get(name, {}).items()
                           if total and label != 'COMPLETE'}
                if count and len(reasons) != 1:
                    raise ValueError('Null reasons cannot be resolved from retained source status.')
                statuses[name] = {'COMPLETE': rows - count}
                if count:
                    statuses[name][next(iter(reasons))] = count
            products[frequency] = dict(file=target.name, rows=rows, bytes=stage.stat().st_size,
                sha256=checksum_path(stage), columns=first['products'][frequency]['columns'],
                null_counts=nulls, value_status_counts=statuses)
        last = catalogs[-1][1]
        study = membership.study_identity['study_config']
        end = date.fromisoformat(last['end_date'])
        requested_end = date.fromisoformat(study['time']['end_exclusive']) - timedelta(days=1)
        catalog = {k: v for k, v in first.items() if k not in ('products', 'acquisition')}
        catalog.update(run_date_utc=utcnow(), method='era5-retained-exact-coastal-r5-v1',
            end_date=str(end), source_coverage_end=last['source_coverage_end'], products=products,
            incomplete_final_day=last.get('incomplete_final_day', False),
            latest_complete_all_metric_day=str(end - timedelta(days=int(last.get('incomplete_final_day', False)))),
            partial_metric_coverage=([dict(date=str(end), metric='PRECIPITATION_MM',
                expected_hours=24, valid_hours=23, value_status='PARTIAL',
                missing_interval_end_utc=str(end + timedelta(days=1))+'T00:00:00Z')]
                if last.get('incomplete_final_day') else []),
            selection='approved coastal plus inland water R5 registry; exact retained values',
            study_identity=membership.study_identity, reporting_cell_count=len(selected),
            membership_sha256=membership.artifact_sha256, mask_sha256=membership.mask_sha256,
            mask_revision=membership.mask_revision,
            requested_start_date=study['time']['start'], requested_end_date=str(requested_end),
            unavailable_interval=dict(start=str(end + timedelta(days=1)), end=str(requested_end),
                reason='No complete source UTC day with required next-midnight accumulation at acquisition'),
            availability=dict(kind='retrospective_reanalysis', historical_available_at_utc=None,
                forecast_eligibility='not established; retrieval is not historical availability'),
            source_epochs=[dict(start_date=c['start_date'], end_date=c['end_date'],
                catalog_sha256=checksum_path(p/'catalog.json'), retained_directory=str(p),
                metadata_sha256=c['source_metadata_sha256'], evidence_sha256=c['source_evidence_sha256'],
                source_coverage_end=c['source_coverage_end'], source_method=c['method']) for p,c in catalogs],
            acquisition=dict(requests=0,response_bytes=0,policy='reuse retained verified products'),
            elapsed_seconds=time.monotonic()-started)
        # No single vintage hash describes the concatenated history.
        catalog.pop('source_metadata_sha256', None)
        catalog.pop('source_evidence_sha256', None)
        publisher.stage_manifest(output/'catalog.json', catalog)
        publisher.publish()
    return catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', required=True)
    parser.add_argument('--study-config', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--control-dir', required=True)
    args = parser.parse_args()
    result = publish_coastal(args.source, args.study_config, args.output_dir, args.control_dir)
    print(json.dumps(dict(end_date=result['end_date'], cells=result['reporting_cell_count'],
        elapsed_seconds=result['elapsed_seconds'], products={k:dict(rows=v['rows'], bytes=v['bytes'])
        for k,v in result['products'].items()}), indent=2))


if __name__ == '__main__':
    main()
