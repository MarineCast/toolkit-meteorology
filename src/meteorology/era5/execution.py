"""Bounded offline one-day native processing; scratch checkpoint, not publication."""
from __future__ import annotations

from datetime import date
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..artifacts import checksum_path
from ..study import load_study_config
from .source import decode_grib
from .daily import utc_daily
from .resources import (Budget,LimitExceeded,POINT_BATCH,PARQUET_BATCH,ROW_BYTES,COPIES,
                        memory_estimate,atomic_json)


def process_native_day(study_config, source_files: list[tuple[Path,str]], destination: Path, *,
                       day: str, budget: Budget, native_source_pilot: bool=False) -> Path:
    """Stream normalization, then aggregate fixed native-point batches in one worker.

    source_files carry captured retrieval instants. Existing source files are read
    only. Output is a fresh, immutable scratch directory; failed/interrupt states
    and partials remain inspectable with no terminal manifest. No provider call or
    marine membership creation occurs. Standalone helper APIs without budget are
    unbounded and are not an approved pilot execution path.
    """
    identity=load_study_config(study_config,planning=True) if native_source_pilot else load_study_config(study_config)
    if identity is None:raise ValueError('Bounded processing requires explicit production-qualified study selection.')
    if not source_files or len(source_files)>4:raise ValueError('One-day processing accepts 1–4 bounded source files.')
    if not date.fromisoformat(identity['requested_time']['start'])<=date.fromisoformat(day)<date.fromisoformat(identity['requested_time']['end_exclusive']):
        raise ValueError('Processing day lies outside requested study window.')
    budget.checkpoint()
    for path,_ in source_files:
        if Path(path).stat().st_size>budget.limits.transfer_bytes:raise LimitExceeded('Captured source chunk exceeds pilot source byte allowance.')
    if sum(Path(p).stat().st_size for p,_ in source_files)>budget.limits.transfer_bytes:
        raise LimitExceeded('Captured input chunks exceed total pilot transfer allowance.')
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False) # exclusive single-run lease; never replace old results
    state=destination/'RUN_STATE.json'
    normalized=destination/'normalized-native.parquet.partial'
    daily=destination/'native-daily.parquet.partial'
    first=pd.Timestamp(day,tz='UTC');last=first+pd.Timedelta(days=1)
    writer=None;daily_writer=None;grid_size=None;grid_hash=None;messages=0
    try:
        budget.check_disk(destination,additional_bytes=65536)
        atomic_json(state,dict(status='NORMALIZING',day=day,limits=vars(budget.limits)))
        for source,retrieved in source_files:
            for frame in decode_grib(source,retrieved_at_utc=retrieved,budget=budget):
                messages+=1
                if messages>2*(7*48+1):raise LimitExceeded('Source message count exceeds a two-expver two-day pilot chunk.')
                valid=pd.Timestamp(frame.VALID_TIME_UTC.iloc[0])
                if valid<first or valid>last or (valid==last and frame.PARAMETER.iloc[0]!='tp'):continue
                n=int(frame.SOURCE_GRID_SIZE.iloc[0]);g=frame.SOURCE_GRID_HASH.iloc[0]
                if grid_size is None:
                    grid_size,grid_hash=n,g
                    model=memory_estimate(n,source_bytes=sum(Path(p).stat().st_size for p,_ in source_files))
                    if model['bounded_working_set_estimate_bytes']>budget.limits.memory_bytes:
                        raise LimitExceeded('Normalized batch working-set estimate exceeds memory cap before staging.')
                    budget.check_disk(destination,additional_bytes=model['normalized_full_day_estimate_bytes'])
                if (n,g)!=(grid_size,grid_hash):raise ValueError('Source grid changed in one-day input.')
                budget.checkpoint(additional_memory=len(frame)*ROW_BYTES*COPIES)
                budget.check_disk(destination,additional_bytes=len(frame)*ROW_BYTES)
                table=pa.Table.from_pandas(frame,preserve_index=False)
                if writer is None:writer=pq.ParquetWriter(normalized,table.schema,compression='zstd',use_dictionary=True)
                writer.write_table(table)
                budget.checkpoint()
        if writer is not None:writer.close();writer=None
        if grid_size is None:raise ValueError('Captured source has no rows in requested UTC day.')
        atomic_json(state,dict(status='AGGREGATING',day=day,grid_points=grid_size,messages=messages))
        for offset in range(0,grid_size,POINT_BATCH):
            indices=set(range(offset,min(offset+POINT_BATCH,grid_size)))
            budget.checkpoint(additional_memory=len(indices)*338*ROW_BYTES*COPIES+PARQUET_BATCH*ROW_BYTES*COPIES)
            parts=[];rows=0
            for batch in pq.ParquetFile(normalized).iter_batches(batch_size=PARQUET_BATCH):
                budget.checkpoint(additional_memory=PARQUET_BATCH*ROW_BYTES*COPIES)
                frame=batch.to_pandas()
                part=frame[frame.SOURCE_GRID_INDEX.isin(indices)].copy()
                if len(part):parts.append(part);rows+=len(part)
                if rows>len(indices)*338:raise LimitExceeded('Per-point time/parameter/expver row cap exceeded.')
            if not parts:raise ValueError('Native point batch absent from source.')
            budget.checkpoint(additional_memory=rows*ROW_BYTES*COPIES)
            records=pd.concat(parts,ignore_index=True)
            result=utc_daily(records,day=day,expected_native_indices=indices,budget=budget)
            budget.check_disk(destination,additional_bytes=len(result)*16*1024)
            # Nullable metric values have stable float schemas even if a whole point batch is missing.
            from .daily import METRICS
            fields=[]
            for column in result:
                if column in {'SOURCE_GRID_INDEX'} or column.endswith(('_VALID_HOURS','_EXPECTED_HOURS')):
                    dtype=pa.int64()
                elif column in {'SOURCE_LAT','SOURCE_LON'} or column.endswith('_COVERAGE_FRACTION') or column in {'ERA5_'+m for m in METRICS}:
                    dtype=pa.float64()
                else:dtype=pa.string()
                fields.append(pa.field(column,dtype))
            table=pa.Table.from_pandas(result,schema=pa.schema(fields),preserve_index=False)
            if daily_writer is None:daily_writer=pq.ParquetWriter(daily,table.schema,compression='zstd')
            daily_writer.write_table(table)
            budget.checkpoint()
        daily_writer.close();daily_writer=None
        budget.check_disk(destination,additional_bytes=65536)
        for path in (normalized,daily):
            with path.open('rb') as f:os.fsync(f.fileno())
        inputs=[dict(sha256=checksum_path(Path(path)),retrieved_at_utc=retrieved) for path,retrieved in source_files]
        receipts=[dict(path=p.name.removesuffix('.partial'),sha256=checksum_path(p),bytes=p.stat().st_size)
                  for p in (normalized,daily)]
        budget.checkpoint()
        os.replace(normalized,destination/'normalized-native.parquet')
        os.replace(daily,destination/'native-daily.parquet')
        manifest=dict(status='COMPLETE_SOURCE_PROCESSING_CHECKPOINT_NOT_FINAL_REPORTING',day=day,
                      shared_study=identity,native_source_pilot=native_source_pilot,
                      final_h3_qualification=False,source_family='ERA5',source_authentication='not_inferred_from_GRIB_headers',
                      inputs=inputs,artifacts=receipts,budget=budget.receipt(),grid_points=grid_size,
                      requested_time=identity['requested_time'],actual_days=[day],period_complete=False)
        atomic_json(destination/'MANIFEST.json',manifest) # terminal commit, written last
        atomic_json(state,dict(status='COMMITTED_CHECKPOINT',day=day))
        return destination/'MANIFEST.json'
    except BaseException:
        atomic_json(state,dict(status='INCOMPLETE_NOT_PUBLISHED',day=day,budget=budget.receipt()))
        raise
    finally:
        if writer is not None:writer.close()
        if daily_writer is not None:daily_writer.close()
