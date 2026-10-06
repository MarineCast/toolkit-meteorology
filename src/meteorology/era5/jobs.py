"""Bounded, provider-injected CDS lifecycle. No concrete network adapter is enabled.

A provider MUST bound every underlying HTTP call/response, disable hidden retries,
redirects and logging, and never forward credentials to result storage. Method
reservations here alone do not prove those transport properties. Do not pass the
stock CDS convenience client: its wait/download paths are deliberately unsupported.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import re
import time

from .planning import DATASET
from .resources import Budget, LimitExceeded, atomic_json, _bounded_transfer_locked

REQUEST_KEYS = {'product_type', 'variable', 'year', 'month', 'day', 'time', 'area',
                'grid', 'data_format', 'download_format'}
METADATA_RESERVATION = 65536


class AmbiguousSubmission(ValueError):
    """A submitted job may exist, but its identifier was not durably captured."""


def request_identity(request: dict, *, study_sha256: str) -> str:
    if set(request) != REQUEST_KEYS or not re.fullmatch('[0-9a-f]{64}', study_sha256):
        raise ValueError('Exact ERA5 request fields and canonical study checksum required.')
    if request['data_format'] != 'grib' or request['download_format'] != 'unarchived':
        raise ValueError('Only unarchived GRIB requests are supported.')
    # Only the provider request and public study checksum are hashed; no credentials.
    value = {'dataset': DATASET, 'request': request, 'study_sha256': study_sha256,
             'purpose': 'native_source_pilot_not_final_H3_qualification'}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def run_job(provider, request: dict, directory: Path, *, study_sha256: str,
            budget: Budget, approved_identity: str, download_reservation: int,
            poll_seconds: float = 5, poll_window_seconds: float = 300,
            max_polls: int = 12, sleep=time.sleep, journal_path: Path | None = None):
    """Resume a known job; never automatically retry an ambiguous submission.

    Explicit approved_identity is a caller authorization witness, not a review
    system. No provider is constructed here. A timeout preserves the remote job ID
    and aggregate counters. Failed/interrupted downloads require a new destination
    after inspection; they are not resumed, overwritten or published as complete.
    """
    identity = request_identity(request, study_sha256=study_sha256)
    if approved_identity != identity:
        raise ValueError('Caller must supply the explicitly approved exact request identity.')
    if (not 0 < poll_seconds <= 60 or not 0 < poll_window_seconds <= 3600
            or type(max_polls) is not int or max_polls <= 0
            or type(download_reservation) is not int or download_reservation <= 0):
        raise ValueError('Positive bounded polling and download limits required.')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    journal=Path(journal_path) if journal_path is not None else directory/'TRANSFER_BUDGET.json'
    if not journal.parent.is_dir():raise ValueError('Owned budget parent must exist.')
    with (journal.parent / '.transfer.lock').open('a+b') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another worker owns this job.') from None
        budget.bind_journal(journal)
        path = directory / 'JOB_STATE.json'
        binding = dict(identity=identity, dataset=DATASET, study_sha256=study_sha256,
                       download_reservation=download_reservation)
        state = json.loads(path.read_text()) if path.exists() else dict(binding, status='PLANNED')
        if any(state.get(k) != v for k, v in binding.items()):
            raise ValueError('Resume requires identical request, study and download cap.')
        if state['status'] == 'COMPLETE_DOWNLOAD_NOT_SCIENTIFICALLY_QUALIFIED':
            target = directory / 'source.grib'
            digest = hashlib.sha256()
            with target.open('rb') as source:
                while chunk := source.read(65536):
                    budget.checkpoint(additional_memory=65536)
                    digest.update(chunk)
            if target.stat().st_size != state['bytes'] or digest.hexdigest() != state['sha256']:
                raise ValueError('Completed immutable download changed.')
            return state
        if state['status'] == 'SUBMITTING_NO_DURABLE_ID':
            raise AmbiguousSubmission('Remote submission may exist; reconcile with provider before any retry.')
        if state['status'] in {'FAILED', 'DOWNLOAD_INCOMPLETE', 'INVALID_DOWNLOAD'}:
            raise ValueError('Terminal failed attempt requires inspection; no automatic overwrite/retry.')

        def save():
            budget.check_disk(directory, additional_bytes=8192)
            atomic_json(path, state)

        def call(method, *args):
            if not getattr(provider,'handles_http_budget',False):
                budget.reserve_transfer(METADATA_RESERVATION)
            elif provider.budget is not budget:
                raise ValueError('HTTP provider must use the lifecycle budget object.')
            try:
                return method(*args)
            finally:
                budget.persist()

        try:
            if state['status'] == 'PLANNED':
                # Write before submit: a crash or unknown network failure must not duplicate jobs.
                state['status'] = 'SUBMITTING_NO_DURABLE_ID'
                save()
                job_id = call(provider.submit, DATASET, request)
                if not isinstance(job_id, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', job_id):
                    raise AmbiguousSubmission('Provider returned no safe durable job identifier.')
                state.update(job_id=job_id, status='SUBMITTED')
                save()
            started = budget.clock()
            status = None
            for _ in range(max_polls):
                budget.checkpoint()
                if budget.clock() - started >= poll_window_seconds:
                    break
                status = call(provider.status, state['job_id'])
                if status == 'successful':
                    break
                if status in {'failed', 'rejected', 'dismissed', 'deleted'}:
                    state['status'] = 'FAILED'
                    save()
                    raise ValueError('Provider job failed; provider error bodies are not persisted.')
                if status not in {'accepted', 'running'}:
                    raise ValueError('Unrecognized provider status.')
                remaining = poll_window_seconds - (budget.clock() - started)
                if remaining <= 0:
                    break
                sleep(min(poll_seconds, remaining))
            else:
                status = None
            if status != 'successful':
                state['status'] = 'POLL_TIMEOUT_RESUMABLE'
                save()
                raise TimeoutError('Polling window exhausted; same remote job may be resumed.')
            # Provider returns only a bounded opener; URLs/tokens never enter receipts.
            opener = call(provider.result_opener, state['job_id'])
            state['status'] = 'DOWNLOADING'
            save()
            try:
                receipt = _bounded_transfer_locked(opener, directory / 'source.grib', budget=budget,
                                           reservation_bytes=download_reservation)
            except BaseException:
                state['status'] = 'DOWNLOAD_INCOMPLETE'
                atomic_json(path, state)
                raise
            transfer = json.loads(receipt.read_text())
            with (directory / 'source.grib').open('rb') as source:
                signature = source.read(4)
            if signature != b'GRIB':
                state['status'] = 'INVALID_DOWNLOAD'
                save()
                raise ValueError('Downloaded payload is not GRIB; no scientific completion receipt.')
            state.update(status='COMPLETE_DOWNLOAD_NOT_SCIENTIFICALLY_QUALIFIED',
                         sha256=transfer['sha256'], bytes=transfer['received_bytes'],
                         retrieved_at_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())
            save()
            return state
        finally:
            budget.persist()
