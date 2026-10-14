"""Generated GRIB provider; exercises real job, source and result caches offline."""

from io import BytesIO
from .request_fixtures import era5_grib
from meteorology.capabilities import ERA5_NAMES


class Response(BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


class Provider:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.calls = []
        self.jobs = {}
        self.temperature = 283.15
        self.missing_hour = None
        self.fail = False

    def factory(self, **kwargs):
        self.budget = kwargs["budget"]
        return self

    def verify_terms(self, terms):
        self.budget.reserve_transfer(65536)
        self.calls.append("terms")

    def submit(self, dataset, selection):
        self.calls.append("submit")
        if self.fail:
            raise RuntimeError("fixture provider failure")
        identity = str(len(self.jobs))
        reverse = {v: k for k, v in ERA5_NAMES.items()}
        path = self.tmp_path / f"provider-{identity}.grib"
        era5_grib(
            path,
            day="-".join(selection[k][0] for k in ("year", "month", "day")),
            parameters=tuple(reverse[v] for v in selection["variable"]),
            area=selection["area"],
            hours=[int(t[:2]) for t in selection["time"]],
            samples_override={"t2m": self.temperature},
            missing_hour=self.missing_hour,
        )
        self.jobs[identity] = path
        return identity

    def status(self, identity):
        self.calls.append("poll")
        return "successful"

    def result_opener(self, identity):
        self.calls.append("result")

        def open_result():
            self.calls.append("download")
            return Response(self.jobs[identity].read_bytes())

        return open_result


def install_provider(tmp_path, monkeypatch):
    provider = Provider(tmp_path)
    monkeypatch.setenv("CDSAPI_KEY", "SYNTHETIC_TOKEN")
    monkeypatch.setattr("meteorology.era5.transport.CDSHTTPProvider", provider.factory)
    return provider
