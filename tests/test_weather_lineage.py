"""Lineage corruption must fail before no-op, acquisition or publication."""
import copy
import json

import pytest

from meteorology import check_weather, get_weather, regional
from meteorology.lineage import validate_lineage
from .test_weather_api import weather as weather


@pytest.mark.parametrize('damage', [
    'empty_epochs', 'missing_epochs', 'outside', 'gap', 'overlap', 'missing_evidence',
    'bad_hash', 'metadata', 'metric_gap', 'duplicate_metric', 'unknown_metric',
    'bad_receipt',
])
def test_invalid_lineage_rejected_before_append_or_noop(weather, monkeypatch, damage):
    result = get_weather('2009-01-01', '2009-01-01', ['temperature'], **weather)
    weather['rebuild'] = False
    get_weather('2009-01-02', '2009-01-02', ['temperature'], **weather)
    catalog = json.loads(result.catalog_path.read_text())
    epoch = catalog['source_epochs'][0]
    if damage == 'empty_epochs':
        catalog['source_epochs'] = []
    elif damage == 'missing_epochs':
        del catalog['source_epochs']
    elif damage == 'outside':
        epoch['start_date'] = '2008-12-31'
    elif damage == 'gap':
        catalog['source_epochs'].pop()
    elif damage == 'overlap':
        epoch['end_date'] = '2009-01-02'
    elif damage == 'missing_evidence':
        catalog['source_evidence_by_sha256'] = {}
    elif damage == 'bad_hash':
        epoch['evidence_sha256'] = 'not-a-hash'
    elif damage == 'metadata':
        epoch['metadata_sha256'] = 'different'
    elif damage == 'metric_gap':
        epoch['metrics']['daily'].pop()
    elif damage == 'duplicate_metric':
        epoch['metrics']['daily'] *= 2
    elif damage == 'unknown_metric':
        epoch['metrics']['daily'].append('unknown')
    else:
        catalog['source_evidence_by_sha256'][epoch['evidence_sha256']]['objects']['new'] = {}
    result.catalog_path.write_text(json.dumps(catalog))
    before = {p: p.read_bytes() for p in (result.catalog_path, result.hourly_path, result.daily_path)}
    monkeypatch.setattr(regional, 'Archive', lambda *a, **k: pytest.fail('source accessed'))
    with pytest.raises(ValueError, match='source lineage'):
        check_weather(result.catalog_path)
    for end in ('2009-01-02', '2009-01-03'):
        with pytest.raises(ValueError, match='source lineage'):
            get_weather('2009-01-01', end, ['temperature'], **weather)
    assert all(p.read_bytes() == content for p, content in before.items())


def test_single_vintage_hash_and_legacy_unscoped_epochs(weather):
    result = get_weather('2009-01-01', '2009-01-01', **weather)
    catalog = json.loads(result.catalog_path.read_text())
    validate_lineage(catalog)
    broken = copy.deepcopy(catalog)
    broken['source_evidence']['objects']['changed'] = {}
    with pytest.raises(ValueError, match='source lineage'):
        validate_lineage(broken)
    weather['rebuild'] = False
    get_weather('2009-01-02', '2009-01-02', **weather)
    catalog = json.loads(result.catalog_path.read_text())
    for epoch in catalog['source_epochs']:
        epoch.pop('metrics')
    validate_lineage(catalog)
    epoch = catalog['source_epochs'][0]
    repair = dict(date='2009-01-01', metric='PRECIPITATION_MM', cells=1,
                  interval_end_utc='2009-01-02T00:00:00Z',
                  evidence_sha256=epoch['evidence_sha256'], metadata_sha256=epoch['metadata_sha256'])
    catalog['endpoint_repairs'] = [repair]
    validate_lineage(catalog)
    for key, value in [('evidence_sha256', 'bad'), ('date', '2008-12-31'),
                       ('interval_end_utc', '2009-01-02T01:00:00Z'), ('cells', 2)]:
        broken = copy.deepcopy(catalog)
        broken['endpoint_repairs'][0][key] = value
        with pytest.raises(ValueError, match='source lineage'):
            validate_lineage(broken)
