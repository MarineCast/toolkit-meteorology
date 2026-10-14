"""Selective source acquisition and bounded, validated native-day processing."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .artifacts import checksum_path
from .era5.resources import ROW_BYTES, COPIES, LimitExceeded
from .era5.blocks import write_point_blocks
from .request import digest
from .request_lineage import era5_evidence, slot_evidence, summarize


def _receipt(path, retrieved, *, uri=None):
    return dict(
        path=str(Path(path).resolve()),
        sha256=checksum_path(Path(path)),
        bytes=Path(path).stat().st_size,
        retrieved_at_utc=retrieved,
        source_uri=uri,
    )


def _sample_hrrr(frame, evidence, support, mappings):
    if support is None:
        w, s, e, n = evidence["request_bbox"]
        return frame[
            (frame.SOURCE_LAT >= s)
            & (frame.SOURCE_LAT <= n)
            & (frame.SOURCE_LON >= w)
            & (frame.SOURCE_LON <= e)
        ].copy()
    from shapely import from_wkb
    from .surface_weather.sampling import build_nearest_grid_crosswalk, validate_crosswalk_distance

    frame.attrs["native_footprint"] = from_wkb(bytes.fromhex(evidence["native_footprint_wkb_hex"]))
    frame.attrs["max_nearest_distance_m"] = evidence["max_nearest_distance_m"]
    key = (
        evidence["source_grid_hash"],
        digest(support.to_dict("records")),
        "nearest-haversine-decoded-footprint-v1",
    )
    if key not in mappings:
        mappings.clear()
        mappings[key] = build_nearest_grid_crosswalk(support, frame)
    mapping = mappings[key]
    validate_crosswalk_distance(mapping, frame, support)
    sampled = (
        frame.set_index("SOURCE_GRID_INDEX", drop=False)
        .loc[mapping.SOURCE_GRID_INDEX]
        .reset_index(drop=True)
    )
    sampled["H3_INDEX"] = mapping.H3_INDEX.to_numpy()
    sampled["H3_RESOLUTION"] = 5
    sampled["SOURCE_GRID_DISTANCE_M"] = mapping.SOURCE_GRID_DISTANCE_M.to_numpy()
    return sampled


def hrrr_day(
    plan,
    day,
    root,
    budget,
    *,
    source_files=None,
    retrieved_at_utc=None,
    geometry=None,
    mappings=None,
    generation="retained",
):
    from .hourly_weather.native_day import NativeCache, NativeHTTP, HOST, ArchiveMissing
    from .hourly_weather.native_source import decode_hour

    fields = plan["required_inputs"]
    frames, receipts = [], []
    forecast_support = []
    grid = None
    for valid in pd.date_range(day, periods=24, freq="h", tz="UTC"):
        budget.checkpoint()
        uri = HOST + f"hrrr.{valid:%Y%m%d}/conus/hrrr.t{valid:%H}z.wrfsfcf00.grib2"
        if source_files is not None:
            supplied = source_files.get(valid.isoformat()) or source_files.get(
                valid.strftime("%Y%m%dT%HZ")
            )
            if supplied is None:
                continue
            files = {name: Path(supplied[name]) for name in fields}
            hour_receipts = [_receipt(path, retrieved_at_utc) for path in files.values()]
        else:
            raw_identity = digest(dict(uri=uri, encoding="GRIB2", generation=generation))
            source_plan = dict(cycles=[dict(uri=uri)], plan_sha256=raw_identity, timeout_seconds=15)
            reader = NativeCache(budget, source_plan)
            http = None
            folder = root / "sources/hrrr" / raw_identity
            try:
                cached_index, _, _ = reader.cached_transfer(
                    uri + ".idx", folder, "index", valid=valid, reservation=65536
                )
                if cached_index is None:
                    http = NativeHTTP(budget, source_plan)
                    cached_index = http.transfer(
                        uri + ".idx", folder, "index", valid=valid, reservation=65536
                    )
                index, imeta = cached_index
            except ArchiveMissing:
                continue
            from .request_sources import hrrr_spans

            selections = []
            for name, span in hrrr_spans(index, valid, fields):
                cached, _, _ = reader.cached_transfer(
                    uri,
                    folder,
                    name,
                    valid=valid,
                    reservation=span[1] - span[0] + 1,
                    byte_range=span,
                )
                selections.append((name, span, cached))
            outstanding = [span for _, span, cached in selections if cached is None]
            if (
                budget.reserved + sum(b - a + 1 for a, b in outstanding)
                > budget.limits.transfer_bytes
                or budget.requests + len(outstanding) > budget.limits.requests
            ):
                raise LimitExceeded("Remaining budget cannot cover selected HRRR cycle.")
            if outstanding and http is None:
                http = NativeHTTP(budget, source_plan)
            files = {}
            hour_receipts = [_receipt(index, imeta["retrieved_at_utc"], uri=uri + ".idx")]
            etags = set()
            for name, span, cached in selections:
                path, meta = cached or http.transfer(
                    uri,
                    folder,
                    name,
                    valid=valid,
                    reservation=span[1] - span[0] + 1,
                    byte_range=span,
                )
                files[name] = path
                etags.add(meta["etag"])
                hour_receipts.append(_receipt(path, meta["retrieved_at_utc"], uri=uri))
            if len(etags) != 1:
                raise ValueError("HRRR fields come from different S3 generations.")
        frame, evidence = decode_hour(
            files,
            valid=valid,
            bbox=plan["request"]["bbox"],
            halo=0.2,
            budget=budget,
            selected_fields=fields,
            geometry_cache=geometry,
        )
        if grid is not None and grid != evidence["source_grid_hash"]:
            raise ValueError("HRRR grid changed inside a UTC request day.")
        grid = evidence["source_grid_hash"]
        for name, path in files.items():
            receipt = next(r for r in hour_receipts if r["path"] == str(path.resolve()))
            forecast_support.append(
                dict(
                    parameter=name,
                    valid_time_utc=valid.isoformat(),
                    init_time_utc=valid.isoformat(),
                    forecast_hour=0,
                    product="sfc",
                    source_sha256=receipt["sha256"],
                    source_grid_hash=evidence["source_grid_hash"],
                    source_wind_basis=evidence["source_wind_basis"],
                )
            )
        evidence["request_bbox"] = plan["request"]["bbox"]
        frame = _sample_hrrr(frame, evidence, plan["support"], mappings)
        if frame.empty:
            raise ValueError(
                "Request bbox contains no native HRRR points; enlarge bbox or choose H3."
            )
        from .hourly_weather.native_source import METRIC_INPUTS

        inverse = {name: metric for metric, (name, _) in METRIC_INPUTS.items()}
        for name, path in files.items():
            receipt = next(r for r in hour_receipts if r["path"] == str(path.resolve()))
            raw = (
                frame["RAW_U_WIND_10M_MS"]
                if name == "u_wind_10m_ms"
                else frame["RAW_V_WIND_10M_MS"]
                if name == "v_wind_10m_ms"
                else frame[inverse[name]]
            )
            frame[name + "__LINEAGE"] = [
                json.dumps(
                    dict(
                        parameter=name,
                        valid_time_utc=valid.isoformat(),
                        source_generation=generation,
                        selected_revision=generation,
                        source_grid_index=int(index),
                        source_grid_hash=evidence["source_grid_hash"],
                        source_sha256=receipt["sha256"],
                        retrieved_at_utc=receipt["retrieved_at_utc"],
                        consolidation_status="operational_analysis",
                        value_status="VALID" if np.isfinite(value) else "BITMAP_NULL",
                        init_time_utc=valid.isoformat(),
                        forecast_hour=0,
                        interval_start_utc=valid.isoformat(),
                    ),
                    sort_keys=True,
                )
                for index, value in zip(frame.SOURCE_GRID_INDEX, raw)
            ]
        frames.append(frame)
        receipts.extend(hour_receipts)
    if not frames:
        raise ValueError("No requested HRRR cycle is available; no source substitution.")
    budget.checkpoint(additional_memory=sum(len(f) for f in frames) * 512 * COPIES)
    output = pd.concat(frames, ignore_index=True)
    output.attrs["source_forecast_support"] = forecast_support
    return output, receipts


def era5_day(
    plan,
    day,
    root,
    run_root,
    budget,
    *,
    source_files=None,
    retrieved_at_utc=None,
    generation="retained",
):
    from .era5.source import decode_grib, consolidate
    from .era5.jobs import run_job, request_identity
    from .era5.credentials import resolve_credentials
    from .era5.transport import CDSHTTPProvider

    parameters = plan["required_inputs"]
    jobs = [
        job
        for job in plan["jobs"]
        if (job["year"][0], job["month"][0], job["day"][0]) == tuple(day.split("-"))
        and len(job["time"]) == 24
    ]
    position = plan["jobs"].index(jobs[0])
    jobs = plan["jobs"][position : position + (2 if set(parameters) & {"tp", "sf"} else 1)]
    if source_files is not None:
        files = source_files[day] if isinstance(source_files, dict) else source_files
        sources = [(Path(p), retrieved_at_utc) for p in files]
    else:
        context = digest(dict(purpose="standalone_source_cache", generation=generation))
        sources = []
        planned = []
        for job in jobs:
            identity = request_identity(job, study_sha256=context)
            directory = root / "sources/era5" / identity
            state = directory / "JOB_STATE.json"
            cached = (
                state.is_file()
                and json.loads(state.read_text()).get("status")
                == "COMPLETE_DOWNLOAD_NOT_SCIENTIFICALLY_QUALIFIED"
            )
            receipt = None
            if cached:
                # Completed lifecycle verification is network-free and requires
                # no provider instance or credentials. Check all cached chunks
                # before opening any missing job in this day.
                receipt = run_job(
                    None,
                    job,
                    directory,
                    study_sha256=context,
                    budget=budget,
                    approved_identity=identity,
                    download_reservation=32 * 1024**2,
                    journal_path=run_root / "TRANSFER_BUDGET.json",
                )
            planned.append((job, identity, directory, receipt))
        provider = None
        if any(receipt is None for _, _, _, receipt in planned):
            credential = resolve_credentials(env_file=plan["credential_env_file"], required=True)
            provider = CDSHTTPProvider(
                key=credential.key,
                budget=budget,
                storage_hosts=("object-store.os-api.cci2.ecmwf.int",),
                download_cap=32 * 1024**2,
            )
            provider.verify_terms([dict(id="cc-by", revision=1)])
        for job, identity, directory, receipt in planned:
            if receipt is None:
                receipt = run_job(
                    provider,
                    job,
                    directory,
                    study_sha256=context,
                    budget=budget,
                    approved_identity=identity,
                    download_reservation=32 * 1024**2,
                    journal_path=run_root / "TRANSFER_BUDGET.json",
                )
            sources.append((directory / "source.grib", receipt["retrieved_at_utc"]))
    first = pd.Timestamp(day, tz="UTC")
    last = first + pd.Timedelta(days=1)
    area = jobs[0]["area"]
    expected_grid = {
        (round(lat, 8), round(lon, 8))
        for lat in np.arange(area[2], area[0] + 0.01, 0.25)
        for lon in np.arange(area[1], area[3] + 0.01, 0.25)
    }
    import uuid

    normalized = run_root / f"{day}-normalized-{uuid.uuid4().hex}.parquet"
    groups, group_index = {}, 0
    writer = None
    budget.begin_write(normalized, 65536)
    grid = None
    count = 0
    forecast_support = []
    try:
        for source, retrieved in sources:
            for frame in decode_grib(
                source,
                retrieved_at_utc=retrieved,
                budget=budget,
                parameters=parameters,
                include_forecast_metadata=True,
            ):
                count += 1
                if count > 2 * (24 * len(parameters) + len(set(parameters) & {"tp", "sf"})) + 4:
                    raise LimitExceeded("ERA5 selected-message count exceeds day/expver allowance.")
                valid = pd.Timestamp(frame.VALID_TIME_UTC.iloc[0])
                parameter = frame.PARAMETER.iloc[0]
                if (
                    valid < first
                    or valid > last
                    or (valid == last and parameter not in {"tp", "sf"})
                ):
                    raise ValueError(
                        "ERA5 delivered time lies outside exact selected day/boundary."
                    )
                if set(zip(frame.SOURCE_LAT, frame.SOURCE_LON)) != expected_grid:
                    raise ValueError("ERA5 delivered grid differs from planned native rectangle.")
                identity = frame.SOURCE_GRID_HASH.iloc[0]
                if grid is not None and grid != identity:
                    raise ValueError("ERA5 grid changed inside UTC request day.")
                grid = identity
                table = pa.Table.from_pandas(frame, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(normalized, table.schema, compression="zstd")
                group_index = write_point_blocks(
                    writer,
                    table,
                    groups,
                    group_index,
                    before_write=lambda rows: budget.reserve_more(normalized, rows * ROW_BYTES),
                )
    finally:
        if writer is not None:
            writer.close()
        budget.finish_write(normalized)
    if grid is None:
        raise ValueError("ERA5 source contains no requested records.")
    pieces = []
    file = pq.ParquetFile(normalized)
    w, s, e, n = plan["request"]["bbox"]
    for offset, addresses in groups.items():
        rows = sum(file.metadata.row_group(i).num_rows for i in addresses)
        budget.checkpoint(additional_memory=rows * ROW_BYTES * COPIES)
        records = file.read_row_groups(addresses).to_pandas()
        records = consolidate(records, expected_native_indices=set(records.SOURCE_GRID_INDEX))
        support_columns = [
            "PARAMETER",
            "VALID_TIME_UTC",
            "INIT_TIME_UTC",
            "FORECAST_HOUR",
            "INTERVAL_START_UTC",
            "EXPVER",
            "SOURCE_SHA256",
            "SOURCE_GRID_HASH",
            "SOURCE_DATA_TYPE",
        ]
        for row in records[support_columns].drop_duplicates().itertuples():
            forecast_support.append(
                dict(
                    parameter=row.PARAMETER,
                    valid_time_utc=row.VALID_TIME_UTC,
                    init_time_utc=row.INIT_TIME_UTC,
                    forecast_hour=int(row.FORECAST_HOUR),
                    interval_start_utc=row.INTERVAL_START_UTC,
                    expver=int(row.EXPVER),
                    source_sha256=row.SOURCE_SHA256,
                    source_grid_hash=row.SOURCE_GRID_HASH,
                    source_data_type=row.SOURCE_DATA_TYPE,
                )
            )
        coords = records[
            ["SOURCE_GRID_INDEX", "SOURCE_LAT", "SOURCE_LON", "SOURCE_GRID_HASH"]
        ].drop_duplicates()
        values = records.pivot(
            index=["SOURCE_GRID_INDEX", "VALID_TIME_UTC"], columns="PARAMETER", values="VALUE"
        )
        for param in parameters:
            if param not in values:
                values[param] = np.nan
        output = pd.DataFrame(index=values.index)
        converts = dict(
            t2m=("TEMPERATURE_2M_C", lambda x: x - 273.15),
            d2m=("DEWPOINT_2M_C", lambda x: x - 273.15),
            u10=("U_WIND_10M_MS", lambda x: x),
            v10=("V_WIND_10M_MS", lambda x: x),
            msl=("MEAN_SEA_LEVEL_PRESSURE_HPA", lambda x: x / 100),
            tcc=("TOTAL_CLOUD_COVER_PCT", lambda x: x * 100),
            tp=("PRECIPITATION_MM", lambda x: x * 1000),
            sf=("SNOWFALL_WATER_EQUIVALENT_MM", lambda x: x * 1000),
        )
        for param, (metric, convert) in converts.items():
            if param in values:
                output[metric] = convert(values[param])
        if "wind" in plan["request"]["variables"]:
            output["WIND_SPEED_10M_MS"] = np.hypot(output.U_WIND_10M_MS, output.V_WIND_10M_MS)
        if "relative_humidity" in plan["request"]["variables"]:
            t, td = output.TEMPERATURE_2M_C, output.DEWPOINT_2M_C
            rh = 100 * np.exp(17.67 * td / (td + 243.5) - 17.67 * t / (t + 243.5))
            if (rh.dropna() > 100.01).any():
                raise ValueError("ERA5 RH exceeds the Bolton water-RH contract; no clipping.")
            output["RELATIVE_HUMIDITY_2M_PCT"] = rh
        records["PARAMETER_LINEAGE"] = [
            era5_evidence(row, generation) for row in records.itertuples()
        ]
        lineage = records.pivot(
            index=["SOURCE_GRID_INDEX", "VALID_TIME_UTC"],
            columns="PARAMETER",
            values="PARAMETER_LINEAGE",
        )
        lineage.columns = [p + "__LINEAGE" for p in lineage.columns]
        output = (
            output.join(lineage)
            .reset_index()
            .merge(coords, on="SOURCE_GRID_INDEX", validate="many_to_one")
        )
        output = output[
            (output.SOURCE_LAT >= s)
            & (output.SOURCE_LAT <= n)
            & (output.SOURCE_LON >= w)
            & (output.SOURCE_LON <= e)
        ]
        pieces.append(output)
    if not pieces or not sum(len(p) for p in pieces):
        raise ValueError("Request bbox has no native ERA5 grid points; enlarge bbox.")
    receipts = [
        _receipt(path, retrieved, uri="cds://reanalysis-era5-single-levels")
        for path, retrieved in sources
    ]
    budget.checkpoint(additional_memory=sum(len(p) for p in pieces) * 512 * COPIES)
    output = pd.concat(pieces, ignore_index=True)
    output.attrs["source_forecast_support"] = forecast_support
    return output, receipts


def reduce_day(hourly, plan, day):
    """Complete-slot sampled means or interval sums; no mean-filled partial days."""
    # The executor captures forecast support before reduction. Pandas propagates
    # attrs via deep copy on every scalar/column lookup; a large support catalog
    # otherwise makes point/slot lineage lookup quadratic in spatial support.
    # Keep the caller's catalog intact, and avoid carrying it through reductions.
    hourly = hourly.copy(deep=False)
    hourly.attrs = {}
    spatial = "H3_INDEX" if plan["request"]["spatial_output"] == "h3" else "SOURCE_GRID_INDEX"
    if hourly.duplicated([spatial, "VALID_TIME_UTC"]).any():
        raise ValueError("Duplicate support/hour keys in normalized request.")
    start = pd.Timestamp(day, tz="UTC")
    coords = ["SOURCE_GRID_INDEX", "SOURCE_LAT", "SOURCE_LON", "SOURCE_GRID_HASH"]
    if spatial == "H3_INDEX":
        coords += ["H3_INDEX", "H3_RESOLUTION", "SOURCE_GRID_DISTANCE_M"]
    pieces = []
    hard_limits = {
        "TEMPERATURE_2M_C": (-123.15, 76.85),
        "DEWPOINT_2M_C": (-123.15, 76.85),
        "U_WIND_10M_MS": (-200, 200),
        "V_WIND_10M_MS": (-200, 200),
        "WIND_SPEED_10M_MS": (0, 200),
        "MEAN_SEA_LEVEL_PRESSURE_HPA": (700, 1200),
        "TOTAL_CLOUD_COVER_PCT": (0, 100),
        "RELATIVE_HUMIDITY_2M_PCT": (0, 100.01),
        "VISIBILITY_KM": (0, 1000),
        "PRECIPITATION_MM": (0, float("inf")),
        "SNOWFALL_WATER_EQUIVALENT_MM": (0, float("inf")),
    }
    for metric in plan["metrics"]:
        values = hourly[metric].to_numpy(float)
        low, high = hard_limits[metric]
        if np.isinf(values).any() or ((values < low) | (values > high)).any():
            raise ValueError(f"{metric} violates request physical acceptance limits.")
    for metric, definition in plan["metrics"].items():
        output = []
        for key, group in hourly.groupby(spatial, sort=True):
            if any(group[c].nunique(dropna=False) != 1 for c in coords):
                raise ValueError("Support coordinates/grid change inside a UTC day.")
            slots = pd.date_range(
                start + pd.Timedelta(hours=1 if definition["reduction"] == "sum" else 0),
                periods=24,
                freq="h",
            )
            indexed = group.set_index(pd.to_datetime(group.VALID_TIME_UTC, utc=True))
            series = indexed[metric].reindex(slots)
            values = series.to_numpy(float)
            finite = np.isfinite(values)
            lineage = dict(
                SOURCE=plan["request"]["source"],
                TIMEZONE="UTC",
                METRIC=metric,
                UNITS=definition["units"],
                METHOD=plan["request"]["method"],
                REDUCTION=definition["reduction"],
                WIND_VECTOR_BASIS="earth_relative" if definition["variable"] == "wind" else None,
                AVAILABLE_AT_UTC=None,
                AVAILABILITY_POLICY="historical_publication_unknown_actual_retrieval_only",
                DATE=day,
                **{c: group[c].iloc[0] for c in coords},
            )
            if plan["request"]["frequency"] == "daily":
                count = int(finite.sum())
                output.append(
                    dict(
                        lineage,
                        **summarize(
                            [
                                d
                                for stamp in slots
                                for d in slot_evidence(
                                    indexed, stamp, metric, plan["request"]["source"]
                                )
                            ]
                        ),
                        VALUE=float(
                            values.sum() if definition["reduction"] == "sum" else values.mean()
                        )
                        if count == 24
                        else None,
                        STATUS="COMPLETE" if count == 24 else "PARTIAL" if count else "UNAVAILABLE",
                        VALID_HOURS=count,
                        EXPECTED_HOURS=24,
                        COVERAGE_FRACTION=count / 24,
                        INTERVAL_START_UTC=start.isoformat(),
                        INTERVAL_END_UTC=(start + pd.Timedelta(days=1)).isoformat(),
                    )
                )
            else:
                for stamp, value, good in zip(slots, values, finite):
                    output.append(
                        dict(
                            lineage,
                            **summarize(
                                slot_evidence(indexed, stamp, metric, plan["request"]["source"])
                            ),
                            VALID_TIME_UTC=stamp.isoformat(),
                            VALUE=float(value) if good else None,
                            STATUS="COMPLETE" if good else "UNAVAILABLE",
                            VALID_HOURS=int(good),
                            EXPECTED_HOURS=1,
                            COVERAGE_FRACTION=float(good),
                            INTERVAL_START_UTC=(
                                stamp - pd.Timedelta(hours=1)
                                if definition["reduction"] == "sum"
                                else stamp
                            ).isoformat(),
                            INTERVAL_END_UTC=stamp.isoformat(),
                        )
                    )
        pieces.append((metric, pd.DataFrame(output)))
    return pieces
