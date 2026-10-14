"""Offline monthly ERA5 backfill planning, separate from qualified short getters."""

from datetime import date, timedelta, datetime, timezone
import math
from pathlib import Path

from .capabilities import dependencies, ERA5_NAMES
from .request import digest, plan_weather
from .era5.resources import Limits

METHOD = "era5-regional-historical-features-v1"
SCHEMA = "meteorology-backfill-date-support-metric-v1"
DEFAULT_VARIABLES = (
    "temperature",
    "wind",
    "relative_humidity",
    "cloud_cover",
    "pressure",
    "precipitation",
    "dewpoint",
)


def source_request(first, last, parameters, area, *, boundary=False):
    return dict(
        product_type=["reanalysis"],
        variable=sorted(ERA5_NAMES[p] for p in parameters),
        year=[str(first.year)],
        month=[f"{first.month:02}"],
        day=[f"{d:02}" for d in range(first.day, last.day + 1)],
        time=["00:00"] if boundary else [f"{h:02}:00" for h in range(24)],
        area=area,
        grid=[0.25, 0.25],
        data_format="grib",
        download_format="unarchived",
    )


def split_chunk(chunk):
    first, last = [date.fromisoformat(chunk[k]) for k in ("start", "end")]
    if first == last:
        raise ValueError(
            "One UTC day exceeds safe chunk resources; increase reviewed caps or reduce extent."
        )
    middle = first + timedelta(days=(last - first).days // 2)
    return [
        dict(
            chunk,
            start=a.isoformat(),
            end=b.isoformat(),
            request=source_request(a, b, chunk["parameters"], chunk["request"]["area"]),
        )
        for a, b in ((first, middle), (middle + timedelta(days=1), last))
    ]


def plan_backfill(
    variables,
    start_date,
    end_date,
    bbox,
    *,
    frequency="daily",
    spatial_output="native",
    workers=2,
    source_cap=64 * 1024**2,
    memory_bytes=1024**3,
    staging_bytes=2 * 1024**3,
    backend="cds",
    study_config=None,
):
    if backend != "cds":
        raise ValueError(
            "ARCO is not scientifically qualified for this product; use backend='cds'."
        )
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Use 1–4 bounded workers.")
    if spatial_output not in {"native", "h3"}:
        raise ValueError("Output must be native or H3 R5.")
    study = None
    if study_config is not None:
        from .study import load_study_config

        study = load_study_config(study_config, planning=True)
        bbox = bbox if bbox is not None else study["study_config"]["domain"]["bbox_wgs84"]
        start_date = start_date or study["requested_time"]["start"]
        if end_date is None:
            available = datetime.now(timezone.utc).date() - timedelta(days=6)
            end_date = min(
                available,
                date.fromisoformat(study["requested_time"]["end_exclusive"]) - timedelta(days=1),
            ).isoformat()
    if start_date is None or end_date is None or bbox is None:
        raise ValueError("Provide dates/bbox or an explicit shared study configuration.")
    first, last = date.fromisoformat(str(start_date)), date.fromisoformat(str(end_date))
    if last < first:
        raise ValueError("end_date precedes start_date.")
    if study is not None and not (
        date.fromisoformat(study["requested_time"]["start"])
        <= first
        <= last
        < date.fromisoformat(study["requested_time"]["end_exclusive"])
    ):
        raise ValueError(
            "Explicit study dates must remain within its declared window; use a standalone bbox for another window."
        )
    # Reuse date, source, rectangle, physical and unit-support preflight without
    # inheriting the short getter's seven-day acquisition schedule.
    probe = plan_weather(
        variables,
        first.isoformat(),
        first.isoformat(),
        bbox,
        "era5",
        frequency=frequency,
        spatial_output="native",
        limits=Limits(
            memory_bytes=min(memory_bytes, 1024**3),
            staging_bytes=min(staging_bytes, 2 * 1024**3),
            transfer_bytes=max(source_cap, 64 * 1024**2),
            requests=1024,
        ),
    )
    plan_weather(
        variables,
        last.isoformat(),
        last.isoformat(),
        bbox,
        "era5",
        frequency=frequency,
        spatial_output="native",
    )
    names, parameters = dependencies(variables, "era5")
    emitted_points = probe["estimated_native_points"]
    if spatial_output == "h3":
        from .spatial_support.build import _support_frame

        w, s, e, n = bbox
        emitted_points = len(_support_frame(dict(min_lon=w, min_lat=s, max_lon=e, max_lat=n), 5))
        if not emitted_points:
            raise ValueError("Request bbox has no H3 R5 centroid support.")
    points = probe["estimated_native_points"]
    area = probe["jobs"][0]["area"]
    chunks, cursor = [], first
    while cursor <= last:
        following = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        end = min(last, following - timedelta(days=1))
        pending = [
            dict(
                start=cursor.isoformat(),
                end=end.isoformat(),
                parameters=parameters,
                request=source_request(cursor, end, parameters, area),
            )
        ]
        while pending:
            chunk = pending.pop(0)
            days = (date.fromisoformat(chunk["end"]) - date.fromisoformat(chunk["start"])).days + 1
            fields = days * 24 * len(parameters)
            numeric = fields * points * 8
            source_estimate = fields * (points * 8 + 1024)
            metrics = sum(
                {"temperature": 3, "wind": 5, "pressure": 2}.get(v, 1)
                if frequency == "daily"
                else (4 if v == "wind" else 1)
                for v in names
            )
            output_reservation = (
                emitted_points * days * metrics * (24 if frequency == "hourly" else 1) * 1536
            )
            # Numeric cubes + decompression/aggregation overlap; shared process RSS
            # is checked again at runtime. GRIB transfer estimate is conservative,
            # not an assumed compression ratio or provider guarantee.
            safe = (
                fields <= 120000
                and source_estimate <= source_cap
                and workers * (numeric * 3 + 32 * 1024**2) + 192 * 1024**2 <= memory_bytes
                and source_cap * workers + numeric * (workers + 1) + output_reservation + 1024**2
                <= staging_bytes
            )
            if not safe:
                pending[0:0] = split_chunk(chunk)
                continue
            chunk.update(
                estimated_source_bytes=source_estimate,
                numeric_cube_bytes=numeric,
                estimated_output_reservation_bytes=output_reservation,
            )
            chunks.append(chunk)
        cursor = following
    accumulated = sorted(set(parameters) & {"tp", "sf"})
    boundary = None
    if accumulated:
        end = last + timedelta(days=1)
        boundary = dict(
            start=end.isoformat(),
            end=end.isoformat(),
            parameters=accumulated,
            boundary=True,
            request=source_request(end, end, accumulated, area, boundary=True),
        )
    producer = digest(
        {
            name: (Path(__file__).parent / name).read_text()
            for name in (
                "backfill.py",
                "backfill_plan.py",
                "backfill_compute.py",
                "backfill_resources.py",
                "backfill_metrics.py",
                "era5/source.py",
            )
        }
    )
    request = dict(
        method=METHOD,
        schema=SCHEMA,
        producer_fingerprint=producer,
        source="era5",
        backend=backend,
        start=first.isoformat(),
        end=last.isoformat(),
        bbox=list(bbox),
        variables=names,
        parameters=parameters,
        frequency=frequency,
        spatial_output=spatial_output,
        h3_resolution=5 if spatial_output == "h3" else None,
        study_identity=study,
    )
    return dict(
        request=request,
        run_id=digest(request),
        chunks=chunks,
        boundary=boundary,
        native_points=points,
        source_cap=source_cap,
        workers=workers,
        memory_bytes=memory_bytes,
        staging_bytes=staging_bytes,
        planned_jobs=len(chunks) + bool(boundary),
        baseline_daily_jobs=((last - first).days + 1) * (1 + bool(accumulated)),
        field_limit=120000,
        network_requests_made=0,
        production_shared_study_release=False,
        source_resolution="CDS 0.25 degree distribution; approximately 31 km model information",
        provider_transfer_estimate="conservative admission bound; actual payload measured",
        max_numeric_cube_bytes=max(c["numeric_cube_bytes"] for c in chunks),
        total_days=(last - first).days + 1,
        queue_time="unknown_until_live_measurement",
        processing_windows=math.ceil(len(chunks) / workers),
    )
