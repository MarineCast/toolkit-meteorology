"""Bounded generated-GRIB/mock-provider lifecycle sequence; no timing thresholds."""

import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

import psutil

sys.path.append(str(Path(__file__).resolve().parents[1]))
from tests.request_provider import Provider
from meteorology import get_temperature
from meteorology.era5.resources import Limits
from meteorology.request_storage import RequestBudget, CONTROL_BYTES
from meteorology.request_lifecycle import read_record
import meteorology.era5.source as source


def main():
    with tempfile.TemporaryDirectory(prefix="meteorology-lifecycle-") as folder:
        root = Path(folder)
        cache = root / "cache"
        provider = Provider(root)
        decode = source.decode_grib
        counters = dict(decodes=0, traversals=0)
        rglob = Path.rglob

        def counted_decode(*args, **kwargs):
            counters["decodes"] += 1
            yield from decode(*args, **kwargs)

        def counted_walk(path, *args, **kwargs):
            counters["traversals"] += 1
            return rglob(path, *args, **kwargs)

        def request(**kwargs):
            return get_temperature(
                "2024-01-01",
                "2024-01-01",
                (-125.25, 49, -125, 49.25),
                "era5",
                cache_dir=cache,
                **kwargs,
            )

        records = []

        def measure(name, action):
            counters.update(decodes=0, traversals=0)
            calls = len(provider.calls)
            jobs = set(provider.jobs)
            start = time.perf_counter()
            with patch.object(Path, "rglob", counted_walk):
                value = action()
            wall = time.perf_counter() - start
            ledger = json.loads((cache / "real/STORAGE_LEDGER.json").read_text())
            pending = read_record(cache / "real/REQUEST_PUBLICATION.json")
            owner = ledger["owners"].get("runs/" + pending["execution_id"], {})
            records.append(
                dict(
                    step=name,
                    provider_calls=len(provider.calls) - calls,
                    transfer_bytes=sum(
                        provider.jobs[job].stat().st_size for job in set(provider.jobs) - jobs
                    ),
                    **counters,
                    staging_bytes=CONTROL_BYTES + owner.get("bytes", 0) + owner.get("reserved", 0),
                    namespace_bytes=ledger["bytes"],
                    outstanding_bytes=ledger["reserved"],
                    seconds=wall,
                    rss_bytes=psutil.Process().memory_info().rss,
                    operational_warnings=getattr(value, "operational_warnings", []),
                )
            )
            return value

        with (
            patch.dict("os.environ", {"CDSAPI_KEY": "SYNTHETIC_TOKEN"}),
            patch("meteorology.era5.transport.CDSHTTPProvider", provider.factory),
            patch.object(source, "decode_grib", counted_decode),
        ):
            measure("initial", request)
            measure("final_hit", request)
            provider.temperature = 293.15
            measure("refresh", lambda: request(frequency="hourly", refresh=True))
            measure(
                "warm_reaggregation",
                lambda: request(limits=Limits(memory_bytes=1024**3, requests=1)),
            )
            pending = read_record(cache / "real/REQUEST_PUBLICATION.json")
            measure(
                "reopen",
                lambda: RequestBudget(
                    Limits(**pending["limits"]),
                    namespace=cache / "real",
                    run_root=cache / "real" / pending["run_path"],
                ),
            )
            provider.temperature = 298.15
            finish = RequestBudget.finish_write

            def fail(self, path):
                if path.name == "ACTIVE_GENERATIONS.json":
                    raise OSError("simulated committed-accounting failure")
                return finish(self, path)

            with patch.object(RequestBudget, "finish_write", fail):
                measure(
                    "committed_accounting_failure",
                    lambda: request(frequency="hourly", refresh=True),
                )
            measure("recovery", lambda: request(frequency="hourly"))
        assert [r["provider_calls"] for r in records] == [5, 0, 5, 0, 0, 5, 0]
        assert [r["decodes"] for r in records] == [1, 0, 1, 1, 0, 1, 0]
        assert [r["traversals"] for r in records] == [1, 0, 1, 1, 1, 1, 1]
        assert records[4]["staging_bytes"] == CONTROL_BYTES
        assert records[-1]["outstanding_bytes"] == 0
        print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
