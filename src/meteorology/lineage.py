"""Read-only checks of Weather's inline evidence and metric-scoped vintages."""
from datetime import date, timedelta
import hashlib
import json


def validate_lineage(catalog):
    """Require an exact date partition per metric, with hash-bound source evidence.

    Legacy epochs without metric scopes apply to every delivered metric. Endpoint
    repairs supplement (rather than replace) the original date/metric vintage.
    Historical catalog byte hashes are receipts, not hashes of reserialized JSON.
    No retained-directory paths are opened or source requests made.
    """
    try:
        _validate(catalog)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError('Invalid Weather source lineage: ' + str(error)) from None


def _validate(catalog):
    first, last = (date.fromisoformat(catalog[k]) for k in ('start_date', 'end_date'))
    metrics = {f: [c['name'] for c in catalog['products'][f]['columns'][2:]]
               for f in ('hourly', 'daily')}
    evidence = dict(catalog.get('source_evidence_by_sha256', {}))
    if 'source_evidence_sha256' in catalog:
        key = catalog['source_evidence_sha256']
        inline = catalog['source_evidence']
        if key in evidence and evidence[key] != inline:
            raise ValueError('conflicting inline source evidence')
        evidence[key] = inline
    for key, record in evidence.items():
        actual = hashlib.sha256(json.dumps(record, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if key != actual or not isinstance(record['objects'], dict):
            raise ValueError('source evidence hash or object receipts differ')

    def check_evidence(record):
        source = evidence[record['evidence_sha256']]
        if source['metadata_sha256'] != record['metadata_sha256']:
            raise ValueError('epoch metadata differs from source evidence')

    epochs = catalog.get('source_epochs')
    if epochs is None:
        epochs = [dict(start_date=str(first), end_date=str(last),
                       metadata_sha256=catalog['source_metadata_sha256'],
                       evidence_sha256=catalog['source_evidence_sha256'])]
    if not isinstance(epochs, list) or not epochs:
        raise ValueError('source epochs must be nonempty')
    intervals = {(f, name): [] for f, names in metrics.items() for name in names}
    for epoch in epochs:
        check_evidence(epoch)
        a, b = (date.fromisoformat(epoch[k]) for k in ('start_date', 'end_date'))
        if not first <= a <= b <= last:
            raise ValueError('source epoch lies outside product dates')
        scope = epoch.get('metrics', metrics)
        if not isinstance(scope, dict) or set(scope) != set(metrics):
            raise ValueError('invalid epoch metric frequencies')
        if not any(scope.values()):
            raise ValueError('empty epoch metric scope')
        for f, names in scope.items():
            if (not isinstance(names, list) or len(names) != len(set(names))
                    or not set(names) <= set(metrics[f])):
                raise ValueError('unknown or duplicate epoch metrics')
            for name in names:
                intervals[f, name].append((a, b))
    for ranges in intervals.values():
        cursor = first
        for a, b in sorted(ranges):
            if a != cursor:
                raise ValueError('source epochs overlap or omit metric dates')
            cursor = b + timedelta(days=1)
        if cursor != last + timedelta(days=1):
            raise ValueError('source epochs omit metric dates')
    repaired = set()
    for repair in catalog.get('endpoint_repairs', []):
        check_evidence(repair)
        day = date.fromisoformat(repair['date'])
        if (not first <= day <= last or day in repaired
                or repair['metric'] != 'PRECIPITATION_MM'
                or any('PRECIPITATION_MM' not in names for names in metrics.values())
                or repair['interval_end_utc'] != str(day + timedelta(days=1)) + 'T00:00:00Z'
                or repair['cells'] != catalog['reporting_cell_count']):
            raise ValueError('invalid endpoint repair support')
        repaired.add(day)
