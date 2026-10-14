"""Shared, source-selective standalone request API. No workspace/global mutation."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from .capabilities import SOURCES, VARIABLES, dependencies
from .era5.resources import Budget, Limits, memory_estimate

METHOD = "meteorology-selective-utc-request-v2"
SCHEMA = "meteorology-date-support-metric-v2"


def fingerprint():
    root = Path(__file__).parent
    names = (
        "request.py",
        "request_adapters.py",
        "request_cache.py",
        "request_vintage.py",
        "request_lineage.py",
        "request_sources.py",
        "request_storage.py",
        "request_lifecycle.py",
        "capabilities.py",
        "era5/source.py",
        "era5/daily.py",
        "era5/blocks.py",
        "era5/jobs.py",
        "era5/transport.py",
        "hourly_weather/native_day.py",
        "era5/resources.py",
        "hourly_weather/native_source.py",
        "surface_weather/sampling.py",
        "surface_weather/source_validation.py",
        "surface_weather/source.py",
    )
    return hashlib.sha256(b"".join((root / n).read_bytes() for n in names)).hexdigest()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def plan_weather(
    variables,
    start_date,
    end_date,
    bbox,
    data_source,
    *,
    frequency="daily",
    spatial_output=None,
    h3_resolution=5,
    cache_dir=None,
    limits=None,
    source_files=None,
    retrieved_at_utc=None,
    credential_env_file=None,
    retained_cache_bytes=None,
):
    """Validate everything before acquisition; date-only endpoints are inclusive."""
    names, inputs = dependencies(variables, data_source)
    try:
        first, inclusive = date.fromisoformat(str(start_date)), date.fromisoformat(str(end_date))
    except (ValueError, TypeError):
        raise ValueError("start_date/end_date must be ISO YYYY-MM-DD dates.") from None
    days = (inclusive - first).days + 1
    if not 1 <= days <= 7:
        raise ValueError("Request 1–7 inclusive UTC days; split longer intervals explicitly.")
    if first < date.fromisoformat(SOURCES[data_source]["earliest_date"]):
        raise ValueError(
            f"{data_source} request adapter starts at {SOURCES[data_source]['earliest_date']}."
        )
    today = datetime.now(timezone.utc).date()
    if inclusive >= today or (data_source == "era5" and inclusive > today - timedelta(days=5)):
        raise ValueError("Requested retrospective source dates are not yet supported/available.")
    try:
        bounds = tuple(float(x) for x in bbox)
    except (TypeError, ValueError):
        raise ValueError("bbox must be WGS84 (west,south,east,north).") from None
    if len(bounds) != 4 or not all(math.isfinite(x) for x in bounds):
        raise ValueError("bbox requires four finite WGS84 bounds.")
    w, s, e, n = bounds
    if not -180 <= w < e <= 180 or not -90 <= s < n <= 90:
        raise ValueError("Invalid bbox; wrapping/dateline rectangles are unsupported.")
    if frequency not in ("daily", "hourly"):
        raise ValueError("frequency must be daily or hourly; both use UTC.")
    spatial_output = spatial_output or SOURCES[data_source]["default_output"]
    if spatial_output not in SOURCES[data_source]["outputs"]:
        raise ValueError(
            f"{data_source} supports {SOURCES[data_source]['outputs']}; ERA5 H3 sampling is unqualified."
        )
    if type(h3_resolution) is not int or h3_resolution != 5:
        raise ValueError('Only qualified H3 R5 support is exposed; choose spatial_output="native".')
    if data_source == "hrrr" and not (-135 <= w < e <= -60 and 20 <= s < n <= 55):
        raise ValueError("HRRR request bbox lies outside the conservative CONUS envelope.")
    # Bounded native crop preflight; actual decoded footprint remains authoritative.
    if data_source == "hrrr":
        estimated_points = math.ceil((e - w + 0.4) * 111 / 3) * math.ceil((n - s + 0.4) * 111 / 3)
        if estimated_points > 4096:
            raise ValueError("HRRR native crop may exceed 4096 points; use a smaller bbox.")
    else:
        area = [
            math.ceil(n * 4) / 4 + 0.25,
            math.floor(w * 4) / 4 - 0.25,
            math.floor(s * 4) / 4 - 0.25,
            math.ceil(e * 4) / 4 + 0.25,
        ]
        if not (-90 <= area[2] < area[0] <= 90 and -180 <= area[1] < area[3] <= 180):
            raise ValueError("ERA5 native sampling halo crosses geographic limits.")
        if not (math.ceil(s * 4) / 4 <= n and math.ceil(w * 4) / 4 <= e):
            raise ValueError("Request bbox has no native ERA5 grid points; enlarge bbox.")
        estimated_points = (round((area[0] - area[2]) * 4) + 1) * (
            round((area[3] - area[1]) * 4) + 1
        )
    if retained_cache_bytes is not None and (
        type(retained_cache_bytes) is not int or retained_cache_bytes <= 0
    ):
        raise ValueError(
            "retained_cache_bytes must be a positive quota or None for unbounded retention."
        )
    limits = limits or Limits(
        memory_bytes=1024**3, staging_bytes=2 * 1024**3, transfer_bytes=512 * 1024**2, requests=1024
    )
    Budget(limits)  # validate injected caps, without network or allocation
    if data_source == "era5":
        model = memory_estimate(
            estimated_points,
            source_bytes=min(limits.transfer_bytes, 32 * 1024**2),
            records_per_point=2
            * (24 * len(set(inputs) - {"tp", "sf"}) + 25 * len(set(inputs) & {"tp", "sf"})),
        )
        if (
            model["bounded_working_set_estimate_bytes"] > limits.memory_bytes
            or estimated_points > 4096
        ):
            raise ValueError("ERA5 conservative working set/crop exceeds request limits.")
    else:
        model = dict(
            bounded_working_set_estimate_bytes=128 * 1024**2 + 1799 * 1059 * 256 + 64 * 1024**2
        )
        if model["bounded_working_set_estimate_bytes"] > limits.memory_bytes:
            raise ValueError("HRRR full-message decoder preallocation exceeds memory cap.")
    support = None
    if spatial_output == "h3":
        from .spatial_support.build import _support_frame

        support = _support_frame(dict(min_lon=w, min_lat=s, max_lon=e, max_lat=n), 5)
        if len(support) > 4096:
            raise ValueError("H3 request support exceeds 4096 cells.")
    last = inclusive + timedelta(days=1)
    jobs = []
    current = first
    accum = sorted(set(inputs) & {"tp", "sf"})
    while current < last:
        if data_source == "era5":
            from .capabilities import ERA5_NAMES

            common = dict(
                product_type=["reanalysis"],
                area=area,
                grid=[0.25, 0.25],
                data_format="grib",
                download_format="unarchived",
            )
            jobs.append(
                dict(
                    common,
                    variable=sorted(ERA5_NAMES[p] for p in inputs),
                    year=[str(current.year)],
                    month=[f"{current.month:02}"],
                    day=[f"{current.day:02}"],
                    time=[f"{h:02}:00" for h in range(24)],
                )
            )
            if accum:
                boundary = current + timedelta(days=1)
                jobs.append(
                    dict(
                        common,
                        variable=[ERA5_NAMES[p] for p in accum],
                        year=[str(boundary.year)],
                        month=[f"{boundary.month:02}"],
                        day=[f"{boundary.day:02}"],
                        time=["00:00"],
                    )
                )
        current += timedelta(days=1)
    nominal_requests = (
        days * 24 * (1 + len(inputs)) if data_source == "hrrr" else len(jobs) * 5 + 2 * days
    )
    metrics = {
        m: dict(units=u, reduction=VARIABLES[v].reduction, variable=v)
        for v in names
        for m, u in zip(VARIABLES[v].metrics, VARIABLES[v].units)
    }
    logical = dict(
        variables=names,
        inputs=inputs,
        source=data_source,
        start=first.isoformat(),
        end_exclusive=last.isoformat(),
        bbox=list(bounds),
        frequency=frequency,
        timezone="UTC",
        spatial_output=spatial_output,
        h3_resolution=5 if spatial_output == "h3" else None,
        method=METHOD,
        schema=SCHEMA,
        producer_fingerprint=fingerprint(),
        evidence_kind="synthetic_fixture" if source_files is not None else "provider_source",
    )
    if limits.staging_bytes < len(json.dumps(logical).encode()) + 65536:
        raise ValueError("Staging cap cannot hold request/control metadata; increase the cap.")
    if source_files is not None:
        from .artifacts import checksum_path

        if not retrieved_at_utc:
            raise ValueError("source_files require captured retrieved_at_utc.")
        from .temporal_products import _utc

        captured = _utc(retrieved_at_utc)
        if captured.date() < last:
            raise ValueError("Captured retrieval must follow the requested retrospective interval.")
        logical["fixture_retrieved_at_utc"] = captured.isoformat()
        if data_source == "hrrr" and not isinstance(source_files, dict):
            raise ValueError("HRRR fixtures require an hour-to-field-path mapping.")
        if data_source == "era5" and days > 1 and not isinstance(source_files, dict):
            raise ValueError("Multi-day ERA5 fixtures require a date-to-source-files mapping.")

        def flatten(value):
            if isinstance(value, dict):
                return [p for entry in value.values() for p in flatten(entry)]
            if isinstance(value, (list, tuple)):
                return [p for entry in value for p in flatten(entry)]
            return [value]

        paths = flatten(source_files)
        if not paths or sum(Path(p).stat().st_size for p in paths) > limits.transfer_bytes:
            raise ValueError("Captured source files are empty or exceed input allowance.")
        logical["fixture_checksums"] = sorted({checksum_path(Path(p)) for p in paths})
    return dict(
        request=logical,
        request_id=digest(logical),
        jobs=jobs,
        metrics=metrics,
        required_inputs=inputs,
        time_boundaries=dict(
            start=first.isoformat() + "T00:00:00Z",
            end_exclusive=last.isoformat() + "T00:00:00Z",
            date_end_inclusive=True,
        ),
        accumulation_boundary=last.isoformat() + "T00:00:00Z" if accum else None,
        support=support,
        estimated_native_points=estimated_points,
        cache_dir=Path(cache_dir or "weather-cache").expanduser().resolve(),
        limits=limits,
        memory_preflight=model,
        nominal_provider_requests=nominal_requests,
        transfer_size="unknown; transfer cap is a reservation limit, not a prediction",
        credential_env_file=credential_env_file,
        retained_cache_bytes=retained_cache_bytes,
    )


def get_weather(
    variables,
    start_date,
    end_date,
    bbox,
    data_source,
    *,
    frequency="daily",
    spatial_output=None,
    h3_resolution=5,
    cache_dir=None,
    limits=None,
    dry_run=False,
    refresh=False,
    result_id=None,
    source_files=None,
    retrieved_at_utc=None,
    credential_env_file=None,
    retained_cache_bytes=None,
):
    """Acquire dependencies once, return disk-backed UTC date/support/metric data.

    source_files is an offline generated-GRIB fixture hook, isolated from real
    caches and always labelled synthetic. Explicit .to_pandas() loads the result.
    """
    from .request_cache import execute_request, preview_request

    if refresh and result_id is not None:
        raise ValueError("refresh and result_id pinning are mutually exclusive.")
    plan = plan_weather(
        variables,
        start_date,
        end_date,
        bbox,
        data_source,
        frequency=frequency,
        spatial_output=spatial_output,
        h3_resolution=h3_resolution,
        cache_dir=cache_dir,
        limits=limits,
        source_files=source_files,
        retrieved_at_utc=retrieved_at_utc,
        credential_env_file=credential_env_file,
        retained_cache_bytes=retained_cache_bytes,
    )
    if result_id is not None and source_files is None:
        from .request_cache import _manifest
        from .request_vintage import CacheError

        if len(result_id) != 64 or any(c not in "0123456789abcdef" for c in result_id):
            raise CacheError("result_id must be a SHA256 identity.")
        locations = [
            plan["cache_dir"] / name
            for name in ("real", "fixtures")
            if (plan["cache_dir"] / name / "results" / result_id).is_dir()
        ]
        if len(locations) != 1:
            raise CacheError("Pinned result must exist unambiguously in the selected cache.")
        _, pinned = _manifest(locations[0], result_id)
        plan["request"]["evidence_kind"] = pinned["request"]["evidence_kind"]
    if dry_run:
        return preview_request(plan, refresh=refresh, result_id=result_id)
    return execute_request(
        plan,
        refresh=refresh,
        result_id=result_id,
        source_files=source_files,
        retrieved_at_utc=retrieved_at_utc,
    )
