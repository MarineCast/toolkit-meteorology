"""Explicit null-only frontier repair with owned transient source storage."""
import json
from datetime import date

from .companions import is_meteorology_companion
from .storage import data_path, is_temporary_directory
from .core.artifacts import TransactionalFamilyPublisher
from .marine_reporting import load_reporting_membership, select_reporting_membership
from .regional import (ALLOWED_VARIABLES, RegionalResult, check_weather, get_data,
                       metric_specs, work_root, source_root, _claim_owned_cache, _clear_owned_cache,
                       _clean_completed_sources)


def refresh_weather(end_date, *, study_config, output_dir, cache_dir=None, workers=1,
                    transfer_bytes=4 * 1024**3, max_seconds=7200, verbosity=False,
                    allow_incomplete_final_day=False):
    """Repair documented endpoint and append through an explicitly checked source day.

    Uses an owned child of the supplied cache base. Successful readback removes
    owned payloads; failures retain resumable sources. Unrelated files are preserved.
    A covered, repaired request is a validated no-op. Existing finite values are never revised.
    """
    output = data_path(output_dir)
    cache = source_root(output, cache_dir).with_name('refresh-' + work_root(output).name)
    resolved_cache = data_path(cache)
    if (cache.is_symlink() or output == resolved_cache or (output in resolved_cache.parents and not resolved_cache.is_relative_to(output/'tmp'))
            or resolved_cache in output.parents):
        raise ValueError('Refresh output and owned source cache must be separate regular directories.')
    member = load_reporting_membership(study_config)
    end = date.fromisoformat(str(end_date))
    with TransactionalFamilyPublisher(work_root(output)) as publisher:
        expected = {'catalog.json', 'meteorology-hourly.parquet', 'meteorology-daily.parquet'}
        if ({p.name for p in output.iterdir() if not is_meteorology_companion(p) and not is_temporary_directory(p)} != expected
                or any(p.is_symlink() for p in output.iterdir())):
            raise ValueError('Refresh requires a dedicated complete weather product.')
        old = json.loads((output / 'catalog.json').read_text())
        member = select_reporting_membership(member, old.get('selection_bbox_wgs84'))
        check_weather(output / 'catalog.json', _membership=member)
        study = member.study_identity['study_config']
        if (old['bbox'] != list(getattr(member, 'selection_bbox_wgs84', None) or study['domain']['bbox_wgs84'])
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
            with TransactionalFamilyPublisher._exclusive_parent(cache):
                _claim_owned_cache(cache, output)
                archive = Archive(cache, old['bbox'], workers=workers,
                    transfer_bytes=transfer_bytes, max_seconds=max_seconds, verbosity=verbosity)
                required = int(pd.Timestamp(last, tz='UTC').timestamp()) + 86400
                if int(archive.times[-1]) < required:
                    # No repair is possible at this frontier. Release probe payloads
                    # after the existing output has passed readback above.
                    repair = False
                    _clear_owned_cache(cache, output)
        if end <= last and not repair:
            _clean_completed_sources(cache, output)
            return RegionalResult(output/'meteorology-hourly.parquet',
                                  output/'meteorology-daily.parquet', output/'catalog.json')
        result = get_data(old['start_date'], max(end, last),
            bbox=old['bbox'], output_dir=output, cache_dir=cache, workers=workers,
            transfer_bytes=transfer_bytes, max_seconds=max_seconds,
            allow_incomplete_final_day=allow_incomplete_final_day,
            variable_list=ALLOWED_VARIABLES, membership=member, verbosity=verbosity,
            existing=old, publisher=publisher, repair_terminal_precipitation=repair, _discard_cache=True)
        check_weather(result.catalog_path, _membership=member)
        return result
