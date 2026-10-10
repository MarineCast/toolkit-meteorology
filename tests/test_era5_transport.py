import io
import json
from urllib.error import HTTPError

import pytest

from meteorology.era5.transport import CDSHTTPProvider, TransportError, API
from meteorology.era5.jobs import request_identity, run_job
from meteorology.era5.resources import Budget, Limits, LimitExceeded
from .test_era5_jobs import REQUEST, SHA

HOST = "object-store.os-api.cci2.ecmwf.int"
CATALOGUE = {
    "id": "reanalysis-era5-single-levels",
    "links": [{"rel": "license", "id": "cc-by", "rev": 1}],
}


class Response(io.BytesIO):
    def __init__(self, value, status=200, headers=None):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        super().__init__(raw)
        self.status = status
        self.headers = {"Content-Length": str(len(raw)), **(headers or {})}


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def provider(tmp_path, *responses, **kwargs):
    b = Budget(Limits(transfer_bytes=1024**2, requests=20), rss=lambda: 0)
    b.bind_journal(tmp_path / "budget.json")
    opener = Opener(*responses)
    p = CDSHTTPProvider(
        key="SECRET_SENTINEL",
        budget=b,
        storage_hosts=(HOST,),
        download_cap=64,
        opener=opener,
        **kwargs,
    )
    return p, b, opener


def test_concrete_end_to_end_no_token_forwarding_and_exact_requests(tmp_path):
    p, b, opener = provider(
        tmp_path,
        Response(CATALOGUE),
        Response({"licences": [{"id": "cc-by", "revision": 1}]}),
        Response({"id": "reanalysis-era5-single-levels"}),
        Response({"links": [{"rel": "monitor", "href": API + "/retrieve/v1/jobs/job-123"}]}, 201),
        Response(
            {
                "jobID": "job-123",
                "processID": "reanalysis-era5-single-levels",
                "status": "successful",
            }
        ),
        Response(
            {
                "asset": {
                    "value": {
                        "href": f"https://{HOST}/result?signature=SIGNED_SECRET",
                        "file:size": 8,
                    }
                }
            }
        ),
        Response(b"GRIBtest"),
    )
    p.verify_terms([{"id": "cc-by", "revision": 1}])
    result = run_job(
        p,
        REQUEST,
        tmp_path / "job",
        study_sha256=SHA,
        budget=b,
        approved_identity=request_identity(REQUEST, study_sha256=SHA),
        download_reservation=64,
        journal_path=tmp_path / "budget.json",
    )
    assert result["status"].startswith("COMPLETE_DOWNLOAD")
    assert b.requests == 7  # terms + process + submit + status + result + download
    assert len(opener.calls) == 7
    for req, timeout in opener.calls[:-1]:
        assert req.get_header("Private-token") == "SECRET_SENTINEL" and 0 < timeout <= 15
    assert opener.calls[-1][0].get_header("Private-token") is None
    assert json.loads(opener.calls[3][0].data) == {"inputs": REQUEST}
    for file in tmp_path.rglob("*.json"):
        assert "SECRET" not in file.read_text() and "signature=" not in file.read_text()


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.invalid/file",
        "http://object-store.os-api.cci2.ecmwf.int/file",
        "https://object-store.os-api.cci2.ecmwf.int.evil.invalid/file",
        "https://user:pass@object-store.os-api.cci2.ecmwf.int/file",
        "https://object-store.os-api.cci2.ecmwf.int:444/file",
    ],
)
def test_unqualified_result_rejected_before_storage_get(tmp_path, url):
    p, b, opener = provider(tmp_path, Response({"asset": {"value": {"href": url, "file:size": 8}}}))
    with pytest.raises(TransportError, match="Unqualified"):
        p.result_opener("job-123")
    assert len(opener.calls) == 1


def test_metadata_cap_before_large_body_read(tmp_path):
    response = Response(b"x" * 100000)
    p, b, _ = provider(tmp_path, response)
    with pytest.raises(LimitExceeded):
        p.status("job-123")
    assert b.received == 0


def test_result_size_checked_before_download(tmp_path):
    p, b, opener = provider(
        tmp_path, Response({"asset": {"value": {"href": f"https://{HOST}/file", "file:size": 100}}})
    )
    with pytest.raises(LimitExceeded):
        p.result_opener("job-123")
    assert len(opener.calls) == 1


def test_http_failure_is_redacted_without_retry(tmp_path):
    err = HTTPError("https://secret.invalid/?TOKEN", 403, "TOKEN", {}, io.BytesIO(b"TOKEN"))
    p, b, opener = provider(tmp_path, err)
    with pytest.raises(TransportError, match="HTTP 403") as error:
        p.status("job-123")
    assert "TOKEN" not in str(error.value) and error.value.__cause__ is None
    assert len(opener.calls) == 1


def test_no_auto_terms_acceptance_or_submit(tmp_path):
    p, b, opener = provider(tmp_path, Response(CATALOGUE), Response({"licences": []}))
    with pytest.raises(TransportError, match="not verified"):
        p.verify_terms([{"id": "cc-by", "revision": 1}])
    with pytest.raises(TransportError, match="terms required"):
        p.submit("reanalysis-era5-single-levels", REQUEST)
    assert len(opener.calls) == 2 and all(req.method == "GET" for req, _ in opener.calls)


def test_storage_length_matches_official_asset(tmp_path):
    p, b, opener = provider(
        tmp_path,
        Response({"asset": {"value": {"href": f"https://{HOST}/file", "file:size": 8}}}),
        Response(b"GRIB"),
    )
    open_result = p.result_opener("job-123")
    with pytest.raises(TransportError, match="differs"):
        open_result()


def test_real_opener_has_no_proxy_and_no_redirect(tmp_path):
    from urllib.request import ProxyHandler
    from meteorology.era5.transport import NoRedirect

    b = Budget(Limits(), rss=lambda: 0)
    p = CDSHTTPProvider(key="secret", budget=b, storage_hosts=(HOST,), download_cap=64)
    assert any(isinstance(h, NoRedirect) for h in p._opener.handlers)
    assert not any(isinstance(h, ProxyHandler) and h.proxies for h in p._opener.handlers)
    assert (
        NoRedirect().redirect_request(None, None, 302, None, None, "https://evil.invalid") is None
    )
