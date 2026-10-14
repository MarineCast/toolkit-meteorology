"""Read-only exact source-cache inspection; no credentials or provider I/O."""

import json
import pandas as pd
from .artifacts import checksum_path
from .request import digest
from .request_vintage import CacheError
from .era5.resources import Budget, LimitExceeded


def hrrr_source(plan, valid, root, generation, budget):
    from .hourly_weather.native_day import NativeCache, HOST

    uri = HOST + f"hrrr.{valid:%Y%m%d}/conus/hrrr.t{valid:%H}z.wrfsfcf00.grib2"
    identity = digest(dict(uri=uri, encoding="GRIB2", generation=generation))
    source_plan = dict(cycles=[dict(uri=uri)], plan_sha256=identity, timeout_seconds=15)
    return uri, root / "sources/hrrr" / identity, NativeCache(budget, source_plan)


def hrrr_spans(index, valid, fields):
    from .hourly_weather.live import FIELDS

    lines = [line for line in index.read_text().splitlines() if line.strip()]
    offsets = [int(line.split(":", 2)[1]) for line in lines]
    if not offsets or offsets[0] < 0 or any(b <= a for a, b in zip(offsets, offsets[1:])):
        raise ValueError("HRRR index offsets are invalid.")
    if any(f"d={valid:%Y%m%d%H}:" not in line for line in lines):
        raise ValueError("HRRR index has wrong requested UTC cycle.")
    selections = []
    for name in fields:
        found = [i for i, line in enumerate(lines) if FIELDS[name] in line]
        if len(found) != 1 or found[0] + 1 >= len(lines):
            raise ValueError(f"Missing/ambiguous/unbounded HRRR index field: {name}")
        i = found[0]
        if not any(marker in lines[i].split(":") for marker in ("anl", "0 hour fcst")):
            raise ValueError("Selected HRRR field is not instantaneous f00.")
        span = (offsets[i], offsets[i + 1] - 1)
        if span[1] - span[0] + 1 > 32 * 1024**2:
            raise LimitExceeded("Selected HRRR message exceeds 32 MiB cap.")
        selections.append((name, span))
    return selections


def era5_source(job, root, generation):
    from .era5.jobs import request_identity

    context = digest(dict(purpose="standalone_source_cache", generation=generation))
    identity = request_identity(job, study_sha256=context)
    return identity, context, root / "sources/era5" / identity


def inspect_sources(plan, root, generation, *, fixtures=False):
    reusable, missing, uncertainties = [], [], []
    requests, reservations = 0, 0
    if fixtures:
        return dict(
            reusable_source_objects=[],
            missing_source_work=[],
            remaining_provider_requests_lower_bound=0,
            remaining_transfer_reservation_bytes=0,
            exact_provider_requests=True,
            uncertainties=[],
            acquisition="supplied_generated_fixtures",
        )
    budget = Budget(plan["limits"])
    if plan["request"]["source"] == "hrrr":
        first = pd.Timestamp(plan["request"]["start"], tz="UTC")
        last = pd.Timestamp(plan["request"]["end_exclusive"], tz="UTC")
        for valid in pd.date_range(first, last, freq="h", inclusive="left"):
            uri, folder, http = hrrr_source(plan, valid, root, generation, budget)
            cached, _, _ = http.cached_transfer(
                uri + ".idx", folder, "index", valid=valid, reservation=65536
            )
            if cached is None:
                missing.append(
                    dict(uri=uri, inputs=plan["required_inputs"], stage="index_then_fields")
                )
                requests += 1 + len(plan["required_inputs"])
                reservations += 65536
                uncertainties.append(
                    "Missing HRRR index: field transfer sizes unknown until index validation; reserve each exact range before opening."
                )
                continue
            index, meta = cached
            reusable.append(dict(path=str(index), bytes=index.stat().st_size, kind="index"))
            etags = set()
            for name, span in hrrr_spans(index, valid, plan["required_inputs"]):
                cached, _, _ = http.cached_transfer(
                    uri,
                    folder,
                    name,
                    valid=valid,
                    reservation=span[1] - span[0] + 1,
                    byte_range=span,
                )
                if cached:
                    path, meta = cached
                    etags.add(meta["etag"])
                    reusable.append(dict(path=str(path), bytes=path.stat().st_size, kind=name))
                else:
                    missing.append(dict(uri=uri, input=name, byte_range=list(span), stage="field"))
                    requests += 1
                    reservations += span[1] - span[0] + 1
            if len(etags) > 1:
                raise CacheError("Cached HRRR fields come from incompatible source generations.")
    else:
        for job in plan["jobs"]:
            identity, context, directory = era5_source(job, root, generation)
            state_path = directory / "JOB_STATE.json"
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            if state.get("status") == "COMPLETE_DOWNLOAD_NOT_SCIENTIFICALLY_QUALIFIED":
                from .era5.jobs import DATASET

                if any(
                    state.get(k) != v
                    for k, v in dict(
                        identity=identity,
                        study_sha256=context,
                        dataset=DATASET,
                        download_reservation=32 * 1024**2,
                    ).items()
                ):
                    raise CacheError(
                        "ERA5 job selection/source identity differs from retained evidence."
                    )
                path = directory / "source.grib"
                receipt_path = directory / "source.grib.receipt.json"
                receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
                if (
                    not path.is_file()
                    or path.stat().st_size != state["bytes"]
                    or checksum_path(path) != state["sha256"]
                    or receipt.get("status") != "COMPLETE"
                    or receipt.get("sha256") != state["sha256"]
                ):
                    raise CacheError("ERA5 retained source bytes/receipt are corrupt.")
                reusable.append(
                    dict(
                        path=str(path),
                        bytes=state["bytes"],
                        kind="exact_ERA5_job",
                        identity=identity,
                    )
                )
            else:
                missing.append(
                    dict(
                        identity=identity,
                        selection=job,
                        status=state.get("status", "MISSING"),
                        stage="CDS_job",
                    )
                )
                requests += 1  # Lower bound only: terms, submission, polling, result discovery and download are charged individually.
                reservations += 32 * 1024**2
        if missing:
            uncertainties.append(
                "CDS terms/catalogue, retries and polling request count is unknown; staged reservations precede every provider call."
            )
    return dict(
        reusable_source_objects=reusable,
        missing_source_work=missing,
        remaining_provider_requests_lower_bound=requests,
        remaining_transfer_reservation_bytes=reservations,
        exact_provider_requests=not uncertainties,
        uncertainties=sorted(set(uncertainties)),
    )
