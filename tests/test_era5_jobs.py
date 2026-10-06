import io
import json

import pytest

from meteorology.era5.jobs import AmbiguousSubmission, request_identity, run_job
from meteorology.era5.resources import Budget, Limits, LimitExceeded

SHA = 'a' * 64
REQUEST = dict(product_type=['reanalysis'], variable=['total_precipitation'],
               year=['2024'], month=['01'], day=['02'], time=['00:00'],
               area=[49.5,-125.5,49,-125], grid=[.25,.25], data_format='grib',
               download_format='unarchived')


class Response(io.BytesIO):
    def __init__(self, payload, declared=None):
        super().__init__(payload)
        self.headers = {'Content-Length': str(len(payload) if declared is None else declared)}


class Provider:
    def __init__(self, statuses=('successful',), payload=b'GRIBtest', declared=None):
        self.statuses = iter(statuses)
        self.payload = payload
        self.declared = declared
        self.submissions = self.opens = 0

    def submit(self, dataset, request):
        self.submissions += 1
        return 'job-123'

    def status(self, job):
        return next(self.statuses, 'running')

    def result_opener(self, job):
        def open_response():
            self.opens += 1
            return Response(self.payload, self.declared)
        return open_response


def budget(**kwargs):
    return Budget(Limits(transfer_bytes=1024**2, requests=20, **kwargs), rss=lambda: 0)


def run(provider, path, **kwargs):
    return run_job(provider, REQUEST, path, study_sha256=SHA,
                   budget=kwargs.pop('budget', budget()),
                   approved_identity=kwargs.pop('approved_identity', request_identity(REQUEST, study_sha256=SHA)),
                   download_reservation=64, sleep=lambda seconds: None, **kwargs)


def test_idempotent_complete_and_tamper(tmp_path):
    p = Provider()
    first = run(p, tmp_path)
    assert run(p, tmp_path) == first
    assert p.submissions == p.opens == 1
    (tmp_path/'source.grib').write_bytes(b'GRIBchanged')
    with pytest.raises(ValueError, match='changed'):
        run(p, tmp_path)


def test_resume_timeout_retains_job_and_budget(tmp_path):
    p = Provider(('running', 'successful'))
    with pytest.raises(TimeoutError):
        run(p, tmp_path, max_polls=1)
    before = json.loads((tmp_path/'TRANSFER_BUDGET.json').read_text())
    assert before['requests'] == 2
    assert run(p, tmp_path)['status'].startswith('COMPLETE_DOWNLOAD')
    after = json.loads((tmp_path/'TRANSFER_BUDGET.json').read_text())
    assert after['requests'] == 5
    assert p.submissions == 1


def test_ambiguous_submit_is_never_repeated(tmp_path):
    class Ambiguous(Provider):
        def submit(self, *args):
            self.submissions += 1
            raise TimeoutError('sensitive signed URL TOKEN')
    p = Ambiguous()
    with pytest.raises(TimeoutError):
        run(p, tmp_path)
    with pytest.raises(AmbiguousSubmission):
        run(p, tmp_path)
    assert p.submissions == 1
    assert 'TOKEN' not in (tmp_path/'JOB_STATE.json').read_text()


@pytest.mark.parametrize('payload,declared', [(b'GRIBx',100), (b'GRIBx',10), (b'HTML',None)])
def test_invalid_download_never_completes(tmp_path, payload, declared):
    with pytest.raises((ValueError, LimitExceeded)):
        run(Provider(payload=payload, declared=declared), tmp_path)
    state = json.loads((tmp_path/'JOB_STATE.json').read_text())
    assert state['status'] in {'DOWNLOAD_INCOMPLETE','INVALID_DOWNLOAD'}
    with pytest.raises(ValueError, match='inspection'):
        run(Provider(), tmp_path)


def test_wrong_approval_before_any_network(tmp_path):
    p = Provider()
    with pytest.raises(ValueError, match='approved'):
        run(p, tmp_path, approved_identity='b'*64)
    assert p.submissions == 0


def test_changed_request_and_caps_fail_resume(tmp_path):
    p = Provider(('running',))
    with pytest.raises(TimeoutError):
        run(p, tmp_path, max_polls=1)
    changed = dict(REQUEST, day=['03'])
    with pytest.raises(ValueError, match='identical'):
        run_job(p, changed, tmp_path, study_sha256=SHA, budget=budget(),
                approved_identity=request_identity(changed, study_sha256=SHA), download_reservation=64)
    with pytest.raises(ValueError, match='original resource caps'):
        run(p, tmp_path, budget=Budget(Limits(), rss=lambda:0))


def test_failed_job_redacted(tmp_path):
    with pytest.raises(ValueError, match='Provider job failed'):
        run(Provider(('failed',)), tmp_path)
    assert json.loads((tmp_path/'JOB_STATE.json').read_text())['status'] == 'FAILED'


def test_elapsed_budget_survives_resume(tmp_path):
    clock = [0]
    b = Budget(Limits(transfer_bytes=1024**2, requests=20, seconds=10), rss=lambda:0, clock=lambda:clock[0])
    p = Provider(('running',))
    def sleep(seconds):
        clock[0] += seconds
    with pytest.raises(TimeoutError):
        run_job(p, REQUEST, tmp_path, study_sha256=SHA, budget=b,
                approved_identity=request_identity(REQUEST, study_sha256=SHA),
                download_reservation=64, poll_window_seconds=5, sleep=sleep)
    resumed = Budget(b.limits, rss=lambda:0, clock=lambda:clock[0])
    resumed.bind_journal(tmp_path/'TRANSFER_BUDGET.json')
    clock[0] += 6
    with pytest.raises(LimitExceeded, match='time cap'):
        run(p, tmp_path, budget=resumed)
    assert p.submissions == 1


def test_request_cap_blocks_result_lookup_and_open(tmp_path):
    p = Provider()
    b = Budget(Limits(transfer_bytes=1024**2, requests=2), rss=lambda:0)
    with pytest.raises(LimitExceeded, match='reservation'):
        run(p, tmp_path, budget=b)
    assert p.submissions == 1 and p.opens == 0
    state = json.loads((tmp_path/'JOB_STATE.json').read_text())
    assert state['job_id'] == 'job-123'
    assert not (tmp_path/'source.grib').exists()


def test_signed_urls_and_tokens_never_enter_receipts(tmp_path):
    p = Provider()
    run(p, tmp_path)
    for path in tmp_path.glob('*.json'):
        text = path.read_text()
        assert 'https://' not in text
        assert 'PRIVATE-TOKEN' not in text
        assert REQUEST['area'].__repr__() not in text


def test_changed_download_reservation_rejected(tmp_path):
    p = Provider(('running',))
    with pytest.raises(TimeoutError):
        run(p, tmp_path, max_polls=1)
    with pytest.raises(ValueError, match='identical'):
        run_job(p, REQUEST, tmp_path, study_sha256=SHA, budget=budget(),
                approved_identity=request_identity(REQUEST, study_sha256=SHA), download_reservation=128)
    assert p.submissions == 1
