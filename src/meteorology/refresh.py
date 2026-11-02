"""Explicit bounded frontier refresh; preserve sources and repair null support only."""
import json
from datetime import date
from pathlib import Path

from .core.artifacts import TransactionalFamilyPublisher
from .marine_reporting import load_reporting_membership
from .regional import (ALLOWED_VARIABLES, RegionalResult, check_weather, get_data,
                       metric_specs, work_root)


def refresh_weather(end_date, *, study_config, output_dir, cache_dir, workers=1,
                    transfer_bytes=4 * 1024**3, max_seconds=7200, verbosity=False,
                    allow_incomplete_final_day=False):
    """Repair documented endpoint and append through an explicitly checked source day.

    Uses a fresh caller-owned cache for new vintages. Retains payloads for restart
    and provenance; never deletes the caller's cache. A covered, repaired request
    is a validated no-op. Existing finite values are never revised.
    """
    output = Path(output_dir).resolve()
    member = load_reporting_membership(study_config)
    end = date.fromisoformat(str(end_date))
    with TransactionalFamilyPublisher(work_root(output)) as publisher:
        expected = {'catalog.json', 'meteorology-hourly.parquet', 'meteorology-daily.parquet'}
        if {p.name for p in output.iterdir()} != expected or any(p.is_symlink() for p in output.iterdir()):
            raise ValueError('Refresh requires a dedicated complete weather product.')
        check_weather(output / 'catalog.json', _membership=member)
        old = json.loads((output / 'catalog.json').read_text())
        study = member.study_identity['study_config']
        if (old['bbox'] != study['domain']['bbox_wgs84']
                or old['start_date'] < study['time']['start']
                or str(end) >= study['time']['end_exclusive']):
            raise ValueError('Refresh differs from approved study envelope or dates.')
        for frequency in ('daily', 'hourly'):
            if {c['name'] for c in old['products'][frequency]['columns'][2:]} != set(metric_specs(frequency)):
                raise ValueError('Frontier refresh requires the full supported metric product.')
        last = date.fromisoformat(old['end_date'])
        repair = old.get('incomplete_final_day', False)
        if end <= last and repair:
            from .regional import Archive
            import pandas as pd
            archive = Archive(cache_dir, old['bbox'], workers=workers,
                transfer_bytes=transfer_bytes, max_seconds=max_seconds, verbosity=verbosity)
            required = int(pd.Timestamp(last, tz='UTC').timestamp()) + 86400
            if int(archive.times[-1]) < required:
                # A pinned frontier remains partial; no unsupported repair or rewrite.
                repair = False
        if end <= last and not repair:
            return RegionalResult(output/'meteorology-hourly.parquet',
                                  output/'meteorology-daily.parquet', output/'catalog.json')
        result = get_data(old['start_date'], max(end, last),
            bbox=old['bbox'], output_dir=output, cache_dir=cache_dir, workers=workers,
            transfer_bytes=transfer_bytes, max_seconds=max_seconds,
            allow_incomplete_final_day=allow_incomplete_final_day,
            variable_list=ALLOWED_VARIABLES, membership=member, verbosity=verbosity,
            existing=old, publisher=publisher, repair_terminal_precipitation=repair)
        check_weather(result.catalog_path, _membership=member)
        return result
