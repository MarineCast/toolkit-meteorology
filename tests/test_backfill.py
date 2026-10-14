"""Generated GRIB through the real monthly job, normalization and publication path."""

from io import BytesIO
import json
import shutil
import threading
import uuid

import pandas as pd
import pytest

from meteorology.backfill import backfill_weather
from meteorology.backfill_plan import plan_backfill
from meteorology.capabilities import ERA5_NAMES
from .request_fixtures import era5_grib

BBOX = (-125.25, 49, -125, 49.25)


class Provider:
    def __init__(self, directory):
        self.directory = directory
        self.jobs, self.calls, self.selections = {}, [], []
        self.lock = threading.RLock()
        self.missing = None
        self.rain_by_month = {}
        self.wind_flip = False
        self.preliminary_cloud = False

    def factory(self, *, budget, **kwargs):
        owner = self

        class Client:
            def verify_terms(self, terms):
                budget.reserve_transfer(65536)
                owner.calls.append("terms")

            def submit(self, dataset, selection):
                with owner.lock:
                    identity = uuid.uuid4().hex
                    target = owner.directory / (identity + ".grib")
                    reverse = {v: k for k, v in ERA5_NAMES.items()}
                    parameters = tuple(reverse[value] for value in selection["variable"])
                    owner.selections.append(selection)
                    with target.open("wb") as merged:
                        for day in selection["day"]:
                            hours = [int(t[:2]) for t in selection["time"]]
                            for ordinal, batch in enumerate(
                                [[hour] for hour in hours] if owner.wind_flip else [hours]
                            ):
                                file = owner.directory / (identity + "-" + day + f"-{ordinal}.grib")
                                fields = [(parameters, 1)]
                                if owner.preliminary_cloud and "tcc" in parameters:
                                    fields = [
                                        (tuple(p for p in parameters if p != "tcc"), 1),
                                        (("tcc",), 5),
                                    ]
                                for subset, expver in fields:
                                    era5_grib(
                                        file,
                                        day=f"{selection['year'][0]}-{selection['month'][0]}-{day}",
                                        parameters=subset,
                                        area=selection["area"],
                                        hours=batch,
                                        rain=owner.rain_by_month.get(selection["month"][0], 0.001),
                                        snow=0.002,
                                        expver=expver,
                                        missing_messages=owner.missing or (),
                                        samples_override={
                                            "u10": 3 if batch[0] < 12 else -3,
                                            "v10": 4 if batch[0] < 12 else -4,
                                        }
                                        if owner.wind_flip
                                        else None,
                                    )
                                    with file.open("rb") as stream:
                                        shutil.copyfileobj(stream, merged)
                    owner.jobs[identity] = target
                    owner.calls.append("submit")
                    return identity

            def status(self, identity):
                owner.calls.append("poll")
                return "successful"

            def result_opener(self, identity):
                owner.calls.append("result")

                def open_result():
                    owner.calls.append("download")
                    response = BytesIO(owner.jobs[identity].read_bytes())
                    response.headers = {"Content-Length": str(owner.jobs[identity].stat().st_size)}
                    return response

                return open_result

        return Client()


def options(tmp_path, provider):
    return dict(
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        _provider_factory=provider.factory,
        workers=2,
    )


def test_multi_year_month_planning_and_single_terminal_boundary():
    plan = plan_backfill(
        ["temperature", "wind", "precipitation", "snowfall"], "2009-01-15", "2019-01-14", BBOX
    )
    assert len(plan["chunks"]) == 121
    assert plan["planned_jobs"] == 122
    assert plan["chunks"][0]["request"]["day"] == [f"{d:02}" for d in range(15, 32)]
    assert plan["chunks"][-1]["request"]["day"][-1] == "14"
    assert plan["boundary"]["request"]["day"] == ["15"]
    assert plan["boundary"]["request"]["time"] == ["00:00"]


def test_temporal_chunks_split_before_exceeding_resource_bound():
    plan = plan_backfill(
        ["temperature", "wind"], "2024-01-01", "2024-01-31", BBOX, source_cap=200000
    )
    assert len(plan["chunks"]) > 1
    assert all(chunk["estimated_source_bytes"] <= 200000 for chunk in plan["chunks"])
    dates = [day for c in plan["chunks"] for day in pd.date_range(c["start"], c["end"])]
    assert len(dates) == len(set(dates)) == 31


def test_full_area_hourly_plan_accounts_for_expanded_output_staging():
    plan = backfill_weather(
        "2024-01-01", "2024-01-31", (-129.7, 45.9, -121.5, 51.5), frequency="hourly", dry_run=True
    )
    assert len(plan["chunks"]) > 1
    assert all(
        c["estimated_output_reservation_bytes"] < plan["staging_bytes"] for c in plan["chunks"]
    )


def test_real_path_monthly_daily_native_and_final_hit(tmp_path):
    provider = Provider(tmp_path)
    result = backfill_weather(
        "2024-01-30",
        "2024-02-02",
        BBOX,
        variables=[
            "temperature",
            "wind",
            "relative_humidity",
            "cloud_cover",
            "pressure",
            "precipitation",
            "dewpoint",
            "snowfall",
        ],
        **options(tmp_path, provider),
    )
    data = result.to_pandas()
    assert result.metadata["status"] == "COMPLETE"
    assert len(provider.selections) == 3
    assert data[data.METRIC == "TEMPERATURE_2M_C_MEAN"].VALUE.tolist() == pytest.approx(
        [10] * 16, abs=1e-3
    )
    assert data[data.METRIC == "PRECIPITATION_MM"].VALUE.tolist() == pytest.approx([24] * 16)
    assert data[data.METRIC == "SNOWFALL_WATER_EQUIVALENT_MM"].VALUE.tolist() == pytest.approx(
        [48] * 16
    )
    assert data[data.METRIC == "WIND_SPEED_10M_MS_MEAN"].VALUE.tolist() == pytest.approx([5] * 16)
    assert data[
        data.METRIC == "WIND_DIRECTION_10M_DEG_FROM_MEAN_VECTOR"
    ].VALUE.tolist() == pytest.approx([216.86989765] * 16)
    assert data.H3_INDEX.isna().all()
    assert set(data.EXPECTED_HOURS) == {24}
    calls = len(provider.calls)
    hit = backfill_weather(
        "2024-01-30",
        "2024-02-02",
        BBOX,
        variables=[
            "temperature",
            "wind",
            "relative_humidity",
            "cloud_cover",
            "pressure",
            "precipitation",
            "dewpoint",
            "snowfall",
        ],
        **options(tmp_path, provider),
    )
    assert hit.cache_hit and len(provider.calls) == calls
    pd.testing.assert_frame_equal(hit.to_pandas(), data)


def test_incomplete_hour_stays_null_with_metric_specific_coverage(tmp_path):
    provider = Provider(tmp_path)
    provider.missing = ((5, "t2m"),)
    result = backfill_weather(
        "2024-02-29",
        "2024-02-29",
        BBOX,
        variables=["temperature", "wind"],
        **options(tmp_path, provider),
    )
    data = result.to_pandas()
    temperature = data[data.METRIC.str.startswith("TEMPERATURE")]
    assert temperature.VALUE.isna().all() and set(temperature.VALID_HOURS) == {23}
    assert set(temperature.STATUS) == {"PARTIAL"}
    assert set(data[data.METRIC.str.startswith("WIND_SPEED")].STATUS) == {"COMPLETE"}
    assert result.metadata["status"] == "PARTIAL"


def test_resume_does_not_reacquire_or_decode_completed_chunks(tmp_path, monkeypatch):
    import meteorology.backfill as backfill

    provider = Provider(tmp_path)

    def stop(progress):
        raise KeyboardInterrupt("bounded interruption after first partition")

    opts = options(tmp_path, provider)
    with pytest.raises(KeyboardInterrupt):
        backfill_weather(
            "2024-01-31",
            "2024-03-01",
            BBOX,
            variables=["temperature", "precipitation"],
            progress=stop,
            **opts,
        )
    calls = len(provider.calls)
    monkeypatch.setattr(
        backfill, "normalize", lambda *a, **k: pytest.fail("completed source decoded again")
    )
    result = backfill_weather(
        "2024-01-31", "2024-03-01", BBOX, variables=["temperature", "precipitation"], **opts
    )
    assert result.metadata["status"] == "COMPLETE" and len(provider.calls) == calls
    assert len(result.metadata["partitions"]) == 3


def test_resume_keeps_original_caps_and_consumed_requests(tmp_path):
    provider = Provider(tmp_path)
    opts = options(tmp_path, provider)
    with pytest.raises(ValueError, match="cap exhausted"):
        backfill_weather(
            "2024-01-31", "2024-02-01", BBOX, variables=["temperature"], max_requests=1, **opts
        )
    calls = len(provider.calls)
    with pytest.raises(ValueError, match="original aggregate"):
        backfill_weather(
            "2024-01-31", "2024-02-01", BBOX, variables=["temperature"], max_requests=100, **opts
        )
    assert len(provider.calls) == calls


def test_corrupt_partition_rebuilds_only_that_month_preserving_old_bytes(tmp_path, monkeypatch):
    import meteorology.backfill as backfill

    provider = Provider(tmp_path)
    opts = options(tmp_path, provider)
    original = backfill_weather("2024-01-31", "2024-02-01", BBOX, variables=["temperature"], **opts)
    expected = original.to_pandas()
    bad = original.directory / original.manifest["partitions"][0]["artifact"]["path"]
    bad.write_bytes(b"retained corruption fixture")
    calls, decodes = len(provider.calls), []
    normalize = backfill.normalize

    def counted(*args, **kwargs):
        decodes.append(args[0]["path"])
        return normalize(*args, **kwargs)

    monkeypatch.setattr(backfill, "normalize", counted)
    repaired = backfill_weather("2024-01-31", "2024-02-01", BBOX, variables=["temperature"], **opts)
    assert len(provider.calls) == calls and len(decodes) == 1
    assert bad.read_bytes() == b"retained corruption fixture"
    pd.testing.assert_frame_equal(repaired.to_pandas(), expected)


def test_cross_month_boundary_uses_next_month_amount_without_extra_job(tmp_path):
    provider = Provider(tmp_path)
    provider.rain_by_month = {"01": 0.001, "02": 0.002}
    result = backfill_weather(
        "2024-01-31", "2024-02-01", BBOX, variables=["precipitation"], **options(tmp_path, provider)
    )
    data = result.to_pandas()
    assert data[data.DATE == "2024-01-31"].VALUE.tolist() == pytest.approx([25] * 4)
    assert data[data.DATE == "2024-02-01"].VALUE.tolist() == pytest.approx([48] * 4)
    assert len(provider.selections) == 3


def test_speed_averages_hourly_magnitude_and_calm_mean_direction_is_missing(tmp_path):
    provider = Provider(tmp_path)
    provider.wind_flip = True
    result = backfill_weather(
        "2024-01-01", "2024-01-01", BBOX, variables=["wind"], **options(tmp_path, provider)
    )
    data = result.to_pandas()
    assert data[data.METRIC == "WIND_SPEED_10M_MS_MEAN"].VALUE.tolist() == pytest.approx([5] * 4)
    assert data[data.METRIC == "U_WIND_10M_MS_MEAN"].VALUE.tolist() == pytest.approx([0] * 4)
    direction = data[data.METRIC.str.startswith("WIND_DIRECTION")]
    assert direction.VALUE.isna().all()
    assert set(direction.VALUE_STATUS) == {"UNDEFINED_CALM_MEAN"}
    assert set(direction.VALID_HOURS) == {24} and set(direction.COVERAGE_FRACTION) == {1}
    assert result.metadata["status"] == "COMPLETE"


def test_cloud_revision_does_not_contaminate_temperature_lineage(tmp_path):
    provider = Provider(tmp_path)
    provider.preliminary_cloud = True
    result = backfill_weather(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        variables=["cloud_cover", "temperature"],
        **options(tmp_path, provider),
    )
    data = result.to_pandas()
    assert set(data[data.METRIC.str.startswith("TEMPERATURE")].CONSOLIDATION_STATUS) == {"final"}
    assert set(data[data.METRIC.str.startswith("TOTAL_CLOUD")].CONSOLIDATION_STATUS) == {
        "contains_preliminary_ERA5T"
    }
    p = result.manifest["partitions"][0]
    companion = json.loads((result.directory / p["partition_manifest"]["path"]).read_text())
    assert companion["lineage"] and all(
        "dependencies" in item for item in companion["lineage"].values()
    )


def test_explicit_provider_size_rejection_splits_without_reposting_parent(tmp_path):
    from meteorology.era5.transport import RequestTooLarge

    provider = Provider(tmp_path)
    factory = provider.factory
    rejected = []

    def smaller(**kwargs):
        client = factory(**kwargs)
        submit = client.submit

        def bounded(dataset, selection):
            if len(selection["day"]) > 1:
                rejected.append(selection)
                raise RequestTooLarge("generated explicit HTTP 413")
            return submit(dataset, selection)

        client.submit = bounded
        return client

    opts = dict(options(tmp_path, provider), _provider_factory=smaller)
    result = backfill_weather("2024-01-01", "2024-01-02", BBOX, variables=["temperature"], **opts)
    assert result.metadata["status"] == "COMPLETE" and len(rejected) == 1
    assert len(provider.selections) == 2
    assert len(result.manifest["partitions"]) == 2
    assert list((tmp_path / "cache").rglob("SPLIT.json"))


def test_h3_mapping_retains_coarse_grid_identity(tmp_path):
    provider = Provider(tmp_path)
    result = backfill_weather(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        variables=["temperature"],
        spatial_output="h3",
        **options(tmp_path, provider),
    )
    data = result.to_pandas()
    assert data.H3_INDEX.notna().all() and data.SOURCE_GRID_INDEX.notna().all()
    assert data.SOURCE_LAT.between(48.75, 49.5).all()
    assert data.SOURCE_GRID_DISTANCE_M.between(0, 40000).all()
    assert set(data.SPATIAL_METHOD) == {"era5-verified-nearest-haversine-r5-v1"}
    assert len(list((tmp_path / "cache").rglob("mappings/*.json"))) == 1


def test_hourly_streaming_and_materialization_guard(tmp_path):
    provider = Provider(tmp_path)
    result = backfill_weather(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        variables=["temperature", "precipitation"],
        frequency="hourly",
        **options(tmp_path, provider),
    )
    data = result.to_pandas()
    assert set(data.EXPECTED_HOURS) == {1}
    assert len(data) == 192
    assert "2024-01-02T00:00:00+00:00" in set(
        data[data.METRIC == "PRECIPITATION_MM"].VALID_TIME_UTC
    )
    with pytest.raises(ValueError, match="materialization"):
        result.to_pandas(max_rows=10)
    assert sum(table.num_rows for table in result.iter_tables()) == 192


def test_public_api_cli_dry_run_has_no_writes(tmp_path, capsys):
    from meteorology import backfill_weather as public
    from meteorology.cli import main

    plan = public("2009-01-01", "2018-12-31", BBOX, dry_run=True, cache_dir=tmp_path / "cache")
    assert plan["planned_jobs"] == 121 and plan["network_requests_made"] == 0
    assert (
        main(
            [
                "backfill",
                "--start-date",
                "2009-01-01",
                "--end-date",
                "2018-12-31",
                "--bbox",
                *map(str, BBOX),
                "--dry-run",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["planned_jobs"] == 121
    assert not (tmp_path / "cache").exists()


def test_independent_months_submit_concurrently_and_duplicate_callers_reuse(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    provider = Provider(tmp_path)
    base = provider.factory
    barrier = threading.Barrier(2)
    arrivals = []

    def factory(**kwargs):
        client = base(**kwargs)
        submit = client.submit

        def concurrent(dataset, selection):
            arrivals.append(selection["month"][0])
            barrier.wait(timeout=15)
            return submit(dataset, selection)

        client.submit = concurrent
        return client

    opts = dict(options(tmp_path, provider), _provider_factory=factory)

    def call():
        return backfill_weather("2024-01-31", "2024-02-01", BBOX, variables=["temperature"], **opts)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = [future.result(timeout=45) for future in [pool.submit(call), pool.submit(call)]]
    assert sorted(arrivals) == ["01", "02"]
    assert len(provider.selections) == 2
    assert a.metadata["result_id"] == b.metadata["result_id"]
    assert a.cache_hit != b.cache_hit


def test_cached_sources_need_no_provider_for_different_output(tmp_path):
    provider = Provider(tmp_path)
    opts = options(tmp_path, provider)
    backfill_weather("2024-01-01", "2024-01-02", BBOX, variables=["temperature", "wind"], **opts)
    calls = len(provider.calls)

    def forbidden(**kwargs):
        pytest.fail("Cached regional sources constructed a provider")

    hourly = backfill_weather(
        "2024-01-01",
        "2024-01-02",
        BBOX,
        variables=["temperature"],
        frequency="hourly",
        **dict(opts, _provider_factory=forbidden),
    )
    assert len(provider.calls) == calls and hourly.metadata["execution"]["provider_requests"] == 0
    assert hourly.to_pandas().VALUE.tolist() == pytest.approx([10] * 192, abs=1e-3)


def test_original_source_corruption_is_preserved_during_recovery(tmp_path):
    provider = Provider(tmp_path)
    opts = options(tmp_path, provider)
    original = backfill_weather("2024-01-01", "2024-01-01", BBOX, variables=["temperature"], **opts)
    source = original.manifest["partitions"][0]["sources"][0]
    path = __import__("pathlib").Path(source["path"])
    path.write_bytes(b"corrupt immutable source fixture")
    with pytest.raises(ValueError, match="corrupt"):
        original.to_pandas()
    submitted = len(provider.selections)
    repaired = backfill_weather("2024-01-01", "2024-01-01", BBOX, variables=["temperature"], **opts)
    assert repaired.manifest["status"] == "COMPLETE"
    assert path.read_bytes() == b"corrupt immutable source fixture"
    assert len(provider.selections) == submitted  # Reuses the known remote job.


def test_post_durable_commit_error_returns_warning_and_resume_confirms(tmp_path, monkeypatch):
    import meteorology.backfill as backfill

    provider = Provider(tmp_path)
    atomic = backfill.atomic_json

    def failed(path, value, **kwargs):
        result = atomic(path, value, **kwargs)
        if __import__("pathlib").Path(path).name.startswith("BACKFILL-"):
            raise OSError("injected post-durable maintenance error")
        return result

    monkeypatch.setattr(backfill, "atomic_json", failed)
    result = backfill_weather(
        "2024-01-01", "2024-01-01", BBOX, variables=["temperature"], **options(tmp_path, provider)
    )
    assert result.metadata["publication_status"] == "COMMITTED_WITH_OPERATIONAL_WARNING"
    assert result.to_pandas().VALUE.notna().all()
    monkeypatch.setattr(backfill, "atomic_json", atomic)
    hit = backfill_weather(
        "2024-01-01", "2024-01-01", BBOX, variables=["temperature"], **options(tmp_path, provider)
    )
    assert hit.cache_hit and hit.metadata["publication_status"] == "COMMITTED"


def test_replacement_without_durability_is_uncertain_not_unpublished(tmp_path, monkeypatch):
    import meteorology.backfill as backfill
    from meteorology.request_lifecycle import PublicationUncertainError

    provider = Provider(tmp_path)
    atomic = backfill.atomic_json

    def unconfirmed(path, value, **kwargs):
        if __import__("pathlib").Path(path).name.startswith("BACKFILL-"):
            atomic(path, value)
            kwargs["on_replace"]()
            raise OSError("injected unknown directory fsync")
        return atomic(path, value, **kwargs)

    monkeypatch.setattr(backfill, "atomic_json", unconfirmed)
    with pytest.raises(PublicationUncertainError):
        backfill_weather(
            "2024-01-01",
            "2024-01-01",
            BBOX,
            variables=["temperature"],
            **options(tmp_path, provider),
        )
    monkeypatch.setattr(backfill, "atomic_json", atomic)
    hit = backfill_weather(
        "2024-01-01", "2024-01-01", BBOX, variables=["temperature"], **options(tmp_path, provider)
    )
    assert hit.cache_hit and len(provider.selections) == 1


def test_shared_study_derives_retrospective_dates_without_production_claim():
    fixture = __import__("pathlib").Path(__file__).parent / "fixtures/study.coastal-policy.v1.json"
    plan = backfill_weather(study_config=fixture, dry_run=True)
    assert plan["request"]["bbox"] == [-129.7, 45.9, -121.5, 51.5]
    assert plan["request"]["study_identity"]
    assert plan["production_shared_study_release"] is False


def test_retention_quota_is_enforced_before_acquisition(tmp_path):
    provider = Provider(tmp_path)
    with pytest.raises(ValueError, match="quota"):
        backfill_weather(
            "2024-01-01",
            "2024-01-01",
            BBOX,
            variables=["temperature"],
            retained_cache_bytes=100000,
            **options(tmp_path, provider),
        )
    assert not provider.calls


def test_declared_download_size_splits_and_keeps_charged_parent(tmp_path):
    provider = Provider(tmp_path)
    original_factory = provider.factory

    def factory(**kwargs):
        client = original_factory(**kwargs)
        opener = client.result_opener

        def result(identity):
            original = opener(identity)

            def open_result():
                response = original()
                if len(provider.selections[list(provider.jobs).index(identity)]["day"]) > 1:
                    response.headers["Content-Length"] = str(kwargs["download_cap"] + 1)
                return response

            return open_result

        client.result_opener = result
        return client

    progress = []
    result = backfill_weather(
        "2024-02-28",
        "2024-02-29",
        BBOX,
        variables=["temperature"],
        progress=progress.append,
        **dict(options(tmp_path, provider), _provider_factory=factory),
    )
    assert len(provider.selections) == 3  # Rejected download and two child jobs.
    assert len(result.manifest["partitions"]) == 2
    assert progress[-1]["completed_days"] == progress[-1]["total_days"] == 2
    ledger = json.loads(next((tmp_path / "cache").rglob("STORAGE_LEDGER.json")).read_text())
    assert not ledger["allocations"]
    split = next((tmp_path / "cache").rglob("SPLIT.json"))
    assert json.loads(split.read_text())["reason"] == "TransferTooLarge"
    assert result.metadata["budget"]["reserved_transfer_bytes"] > 3 * 1024**2


def test_streamed_export_is_atomic_and_preserves_existing_evidence(tmp_path, monkeypatch):
    from meteorology.backfill import BackfillResult

    provider = Provider(tmp_path)
    result = backfill_weather(
        "2024-01-01", "2024-01-01", BBOX, variables=["temperature"], **options(tmp_path, provider)
    )
    target = tmp_path / "export.parquet"
    iterate = BackfillResult.iter_tables

    def interrupted(self):
        yield next(iterate(self))
        raise KeyboardInterrupt("export interrupted")

    monkeypatch.setattr(BackfillResult, "iter_tables", interrupted)
    with pytest.raises(KeyboardInterrupt):
        result.to_parquet(target)
    assert not target.exists() and list(tmp_path.glob("export.parquet.*.partial"))
    monkeypatch.setattr(BackfillResult, "iter_tables", iterate)
    result.to_parquet(target)
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        result.to_parquet(target)
    assert target.read_bytes() == original


@pytest.mark.parametrize("caps", [{"max_requests": 0}, {"transfer_bytes": -1}, {"max_seconds": 0}])
def test_invalid_aggregate_caps_fail_during_dry_run(caps):
    with pytest.raises(ValueError, match="caps"):
        backfill_weather("2024-01-01", "2024-01-01", BBOX, dry_run=True, **caps)


def test_transient_download_resumes_remote_job_without_resubmission(tmp_path, monkeypatch):
    from meteorology.era5.transport import TransientTransportError
    import meteorology.backfill as backfill

    monkeypatch.setattr(backfill.time, "sleep", lambda _: None)
    provider = Provider(tmp_path)
    factory = provider.factory
    attempts = []

    def flaky(**kwargs):
        client = factory(**kwargs)
        result = client.result_opener

        def opener(identity):
            original = result(identity)

            def open_result():
                attempts.append(identity)
                if len(attempts) == 1:
                    raise TransientTransportError("injected download connection loss")
                return original()

            return open_result

        client.result_opener = opener
        return client

    result = backfill_weather(
        "2024-01-01",
        "2024-01-01",
        BBOX,
        variables=["temperature"],
        **dict(options(tmp_path, provider), _provider_factory=flaky),
    )
    assert result.metadata["status"] == "COMPLETE"
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert len(provider.selections) == 1


def test_adaptive_resume_skips_published_children_without_decode(tmp_path, monkeypatch):
    import meteorology.backfill as backfill
    from meteorology.era5.transport import RequestTooLarge

    provider = Provider(tmp_path)
    factory = provider.factory

    def smaller(**kwargs):
        client = factory(**kwargs)
        submit = client.submit

        def bounded(dataset, selection):
            if len(selection["day"]) > 1:
                raise RequestTooLarge("explicit generated rejection")
            return submit(dataset, selection)

        client.submit = bounded
        return client

    process = backfill.process

    def interrupted(item, *args):
        if item["chunk"]["start"] == "2024-01-02":
            raise KeyboardInterrupt("stop after first child")
        return process(item, *args)

    opts = dict(options(tmp_path, provider), _provider_factory=smaller)
    monkeypatch.setattr(backfill, "process", interrupted)
    with pytest.raises(KeyboardInterrupt):
        backfill_weather("2024-01-01", "2024-01-02", BBOX, variables=["temperature"], **opts)
    monkeypatch.setattr(backfill, "process", process)

    def forbidden(*args, **kwargs):
        raise AssertionError("Completed child or valid pending scratch decoded again")

    monkeypatch.setattr(backfill, "normalize", forbidden)
    result = backfill_weather("2024-01-01", "2024-01-02", BBOX, variables=["temperature"], **opts)
    assert result.metadata["status"] == "COMPLETE"
    assert result.metadata["execution"]["provider_requests"] == 0
