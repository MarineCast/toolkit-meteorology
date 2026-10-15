"""Single-pass strict GRIB normalization and bounded daily historical features."""

import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from .artifacts import checksum_path
from .era5.source import decode_grib
from .era5.resources import atomic_json
from .request import digest
from .backfill_plan import METHOD, SCHEMA

BASE = {
    "temperature": ("TEMPERATURE_2M_C", "degC", ("t2m",), ("mean", "min", "max")),
    "dewpoint": ("DEWPOINT_2M_C", "degC", ("d2m",), ("mean",)),
    "relative_humidity": ("RELATIVE_HUMIDITY_2M_PCT", "percent", ("t2m", "d2m"), ("mean",)),
    "cloud_cover": ("TOTAL_CLOUD_COVER_PCT", "percent", ("tcc",), ("mean",)),
    "pressure": ("MEAN_SEA_LEVEL_PRESSURE_HPA", "hPa", ("msl",), ("mean", "min")),
    "precipitation": ("PRECIPITATION_MM", "mm", ("tp",), ("sum",)),
    "snowfall": ("SNOWFALL_WATER_EQUIVALENT_MM", "mm water equivalent", ("sf",), ("sum",)),
}
WIND = (
    ("U_WIND_10M_MS", "m/s", ("u10",), ("mean",)),
    ("V_WIND_10M_MS", "m/s", ("v10",), ("mean",)),
    ("WIND_SPEED_10M_MS", "m/s", ("u10", "v10"), ("mean", "max")),
    ("WIND_DIRECTION_10M_DEG_FROM", "degrees", ("u10", "v10"), ("mean_vector",)),
)


def normalize(source, selection, destination, budget, *, validated_subset=False):
    """Numeric hourly scratch is bounded by the admitted source chunk, not history.

    Metadata is per parameter/hour. Final expver overrides preliminary even when
    its values are missing; duplicate same-expver messages fail explicitly.
    """
    from .capabilities import ERA5_NAMES

    reverse = {value: key for key, value in ERA5_NAMES.items()}
    parameters = [reverse[value] for value in selection["variable"]]
    first = pd.Timestamp(
        f"{selection['year'][0]}-{selection['month'][0]}-{selection['day'][0]}", tz="UTC"
    )
    hours = [int(value[:2]) for value in selection["time"]]
    stamps = [first.replace(day=int(day), hour=hour) for day in selection["day"] for hour in hours]
    lookup = {stamp: position for position, stamp in enumerate(stamps)}
    area = selection["area"]
    expected = np.array(
        [
            (lat, lon)
            for lat in np.arange(area[2], area[0] + 0.01, 0.25)
            for lon in np.arange(area[1], area[3] + 0.01, 0.25)
        ]
    )
    size = len(expected)
    budget.checkpoint(additional_memory=len(parameters) * len(stamps) * size * 8 * 3)
    values = np.full((len(parameters), len(stamps), size), np.nan)
    revisions = np.zeros((len(parameters), len(stamps)), dtype=np.uint8)
    seen = set()
    evidence, grid, count = {}, None, 0
    started = time.perf_counter()
    for frame in decode_grib(
        source["path"],
        retrieved_at_utc=source["retrieved_at_utc"],
        parameters=parameters,
        budget=budget,
        include_forecast_metadata=True,
        valid_times={stamp.isoformat() for stamp in stamps} if validated_subset else None,
    ):
        parameter, stamp = frame.PARAMETER.iloc[0], pd.Timestamp(frame.VALID_TIME_UTC.iloc[0])
        if stamp not in lookup:
            raise ValueError("ERA5 delivered time outside its exact chunk selection.")
        coordinates = frame[["SOURCE_LAT", "SOURCE_LON"]].to_numpy()
        if coordinates.shape != expected.shape or not np.allclose(
            coordinates, expected, rtol=0, atol=1e-8
        ):
            raise ValueError("ERA5 delivered coordinates differ from its planned regional grid.")
        current = frame.SOURCE_GRID_HASH.iloc[0]
        if grid is not None and current != grid:
            raise ValueError("ERA5 source grid changed within chunk.")
        grid = current
        p, h, revision = parameters.index(parameter), lookup[stamp], int(frame.EXPVER.iloc[0])
        if (p, h, revision) in seen:
            raise ValueError("Duplicate same-expver message requires source adjudication.")
        seen.add((p, h, revision))
        count += 1
        if revisions[p, h] and revisions[p, h] < revision:
            continue
        values[p, h] = frame.VALUE.to_numpy()
        revisions[p, h] = revision
        row = frame.iloc[0]
        evidence[f"{p}/{h}"] = dict(
            parameter=parameter,
            valid_time_utc=stamp.isoformat(),
            interval_start_utc=row.INTERVAL_START_UTC,
            init_time_utc=row.INIT_TIME_UTC,
            forecast_hour=int(row.FORECAST_HOUR),
            selected_revision=revision,
            retrieved_at_utc=row.RETRIEVED_AT_UTC,
            source_sha256=row.SOURCE_SHA256,
            source_grid_hash=grid,
        )
    if grid is None:
        raise ValueError("No usable ERA5 messages in source chunk.")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with budget.write_file(destination, values.nbytes + revisions.nbytes + 1024**2):
        with destination.open("xb") as stream:
            np.savez_compressed(
                stream,
                values=values,
                revisions=revisions,
                coordinates=expected,
                times=np.array([s.value for s in stamps]),
                parameters=np.array(parameters),
            )
    metadata = dict(
        version=1,
        grid_hash=grid,
        evidence=evidence,
        source=source,
        selection=selection,
        artifact_sha256=checksum_path(destination),
        messages=count,
        decode_seconds=time.perf_counter() - started,
    )
    metadata["sha256"] = digest(metadata)
    budget.write_json(destination.with_suffix(".json"), metadata)
    return destination


def read_cube(path):
    path = Path(path)
    metadata = json.loads(path.with_suffix(".json").read_text())
    if (
        metadata.get("version") != 1
        or metadata.get("sha256") != digest({k: v for k, v in metadata.items() if k != "sha256"})
        or checksum_path(path) != metadata["artifact_sha256"]
    ):
        raise ValueError("Normalized chunk scratch checksum differs; retain and reconcile.")
    with np.load(path, allow_pickle=False) as stored:
        result = {key: stored[key] for key in stored.files}
    result["metadata"] = metadata
    return result


def series(cube, boundary, parameter, stamps):
    values, evidence = [], []
    for stamp in stamps:
        current = cube
        positions = np.where(current["times"] == stamp.value)[0]
        if not len(positions) and boundary is not None:
            current = boundary
            positions = np.where(current["times"] == stamp.value)[0]
        if len(positions) and parameter in current["parameters"]:
            p = list(current["parameters"]).index(parameter)
            h = int(positions[0])
            values.append(current["values"][p, h])
            evidence.append(
                current["metadata"]["evidence"].get(f"{p}/{h}")
                or dict(
                    parameter=parameter,
                    valid_time_utc=stamp.isoformat(),
                    value_status="MISSING_MESSAGE",
                )
            )
        else:
            values.append(np.full(cube["values"].shape[-1], np.nan))
            evidence.append(
                dict(
                    parameter=parameter,
                    valid_time_utc=stamp.isoformat(),
                    value_status="MISSING_MESSAGE",
                )
            )
    return np.array(values), evidence


def hour_bits(mask, ids):
    """Pack at most one UTC day's flags, with the first hour in the low bit."""
    weights = np.left_shift(np.uint32(1), np.arange(len(mask), dtype=np.uint32))
    packed = (mask[:, ids] * weights[:, None]).sum(axis=0, dtype=np.uint32)
    return [format(int(value), "x") for value in packed]


def mapping(cube, bbox, output, cache):
    coordinates = cube["coordinates"]
    w, s, e, n = bbox
    if output == "native":
        ids = np.flatnonzero(
            (coordinates[:, 0] >= s)
            & (coordinates[:, 0] <= n)
            & (coordinates[:, 1] >= w)
            & (coordinates[:, 1] <= e)
        )
        return ids, [None] * len(ids), np.zeros(len(ids)), "native_grid_points_inside_bbox_v1"
    from .spatial_support.build import _support_frame
    import h3

    support = _support_frame(dict(min_lon=w, min_lat=s, max_lon=e, max_lat=n), 5)
    cells = support.H3_INDEX.tolist()
    if not cells:
        raise ValueError("Request bbox has no H3 R5 centroid support.")
    targets = np.array([h3.cell_to_latlng(cell) for cell in cells])
    identity = dict(
        method="era5-verified-nearest-haversine-r5-v1",
        grid=cube["metadata"]["grid_hash"],
        bbox=list(bbox),
        cells=cells,
    )
    if (
        targets[:, 0].min() < coordinates[:, 0].min() - 0.125
        or targets[:, 0].max() > coordinates[:, 0].max() + 0.125
        or targets[:, 1].min() < coordinates[:, 1].min() - 0.125
        or targets[:, 1].max() > coordinates[:, 1].max() + 0.125
    ):
        raise ValueError("H3 centroid lies outside the decoded regular-grid footprint.")
    allowance = float(
        np.hypot(111195 * 0.125, 111195 * 0.125 * np.cos(np.radians(targets[:, 0])).max()) * 1.02
    )
    identity["distance_allowance_m"] = allowance
    path = Path(cache) / (digest(identity) + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        previous = json.loads(path.read_text())
        if (
            previous.get("sha256") != digest({k: v for k, v in previous.items() if k != "sha256"})
            or any(previous.get(k) != v for k, v in identity.items())
            or len(previous.get("indices", [])) != len(cells)
            or len(previous.get("distances_m", [])) != len(cells)
            or any(type(i) is not int or not 0 <= i < len(coordinates) for i in previous["indices"])
            or any(not np.isfinite(d) or d < 0 or d > allowance for d in previous["distances_m"])
        ):
            raise ValueError("Cached ERA5-H3 crosswalk is corrupt or incompatible.")
        return (
            np.array(previous["indices"]),
            cells,
            np.array(previous["distances_m"]),
            identity["method"],
        )
    # Calculate against the verified actual source coordinates. Cache reuse must
    # reproduce these indices/distances, not trust the existence of a crosswalk.
    nearest, distances = [], []
    source_radians = np.radians(coordinates)
    for offset in range(0, len(targets), 128):
        target = np.radians(targets[offset : offset + 128])
        dlat = source_radians[None, :, 0] - target[:, None, 0]
        dlon = source_radians[None, :, 1] - target[:, None, 1]
        a = (
            np.sin(dlat / 2) ** 2
            + np.cos(target[:, None, 0])
            * np.cos(source_radians[None, :, 0])
            * np.sin(dlon / 2) ** 2
        )
        distance = 2 * 6371008.8 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        ids = distance.argmin(axis=1)
        nearest.extend(ids.tolist())
        distances.extend(distance[np.arange(len(ids)), ids].tolist())
    if not cells or max(distances) > allowance:
        raise ValueError("H3 support lacks qualified nearby ERA5 native samples.")
    record = dict(
        identity,
        indices=nearest,
        distances_m=distances,
        effective_resolution="approximately 31 km; H3 adds no independent atmospheric information",
    )
    record["sha256"] = digest(record)
    atomic_json(path, record)
    return np.array(nearest), cells, np.array(distances), identity["method"]


def day_frames(cube, boundary, day, request, spatial, lineage):
    """Emit one day/metric at a time, after temporal reduction on native points."""
    if boundary is not None and (
        boundary["metadata"]["grid_hash"] != cube["metadata"]["grid_hash"]
    ):
        raise ValueError("Boundary grid differs from the primary source grid.")
    ids, cells, distance, spatial_method = spatial
    coords = cube["coordinates"][ids]
    start = pd.Timestamp(day, tz="UTC")
    specifications = []
    for variable in request["variables"]:
        specifications.extend(WIND if variable == "wind" else [BASE[variable]])
    # Reuse the same native hourly inputs for wind components, speed/direction,
    # temperature and RH. This cache is bounded to one day, not the source chunk.
    inputs = {}
    for metric, units, parameters, aggregates in specifications:
        accumulated = parameters[0] in {"tp", "sf"}
        stamps = pd.date_range(start + pd.Timedelta(hours=int(accumulated)), periods=24, freq="h")
        arrays, dependencies = {}, []
        for parameter in parameters:
            key = (parameter, accumulated)
            if key not in inputs:
                inputs[key] = series(cube, boundary, parameter, stamps)
            values, evidence = inputs[key]
            arrays[parameter] = values
            dependencies.extend(evidence)
        if metric.startswith("WIND_SPEED"):
            hourly = np.hypot(arrays["u10"], arrays["v10"])
        elif metric.startswith("WIND_DIRECTION"):
            hourly = np.hypot(arrays["u10"], arrays["v10"])
        elif metric.startswith("RELATIVE_HUMIDITY"):
            t, td = arrays["t2m"] - 273.15, arrays["d2m"] - 273.15
            hourly = 100 * np.exp(17.67 * td / (td + 243.5) - 17.67 * t / (t + 243.5))
        else:
            hourly = arrays[parameters[0]].copy()
            if parameters[0] in {"t2m", "d2m"}:
                hourly -= 273.15
            elif parameters[0] == "msl":
                hourly /= 100
            elif parameters[0] == "tcc":
                hourly *= 100
            elif accumulated:
                hourly *= 1000
        joint = np.logical_and.reduce([np.isfinite(values) for values in arrays.values()])
        invalid = np.zeros_like(joint)
        if metric.startswith("RELATIVE_HUMIDITY"):
            invalid = joint & (
                ~np.isfinite(hourly) | (hourly < 0) | (hourly > 100.01) | (td < -123.15)
            )
            joint = joint & ~invalid
        if request["frequency"] == "hourly":
            selections = [(position, slice(position, position + 1)) for position in range(24)]
        else:
            selections = [(None, slice(None))]
        for position, selected in selections:
            expected = 24 if position is None else 1
            valid = joint[selected]
            counts = valid.sum(axis=0)
            invalid_counts = invalid[selected].sum(axis=0)
            deps = (
                dependencies
                if position is None
                else [dependencies[p * 24 + position] for p in range(len(parameters))]
            )
            catalog = dict(
                dependencies=deps, parameters=parameters, grid_hash=cube["metadata"]["grid_hash"]
            )
            if metric.startswith("RELATIVE_HUMIDITY"):
                catalog["derivation_policy"] = "bolton-water-rh-0-to-100.01-unsupported-null-v2"
            lineage_id = digest(catalog)
            lineage[lineage_id] = catalog
            bits = {
                p: hour_bits(~np.isfinite(values[selected]), ids) for p, values in arrays.items()
            }
            # Every aggregate shares its source support. Most points also share
            # one flag pattern; serialize each distinct pattern just once.
            patterns, input_missing = {}, []
            for flags in zip(*(bits[p] for p in parameters)):
                if flags not in patterns:
                    patterns[flags] = json.dumps(dict(zip(parameters, flags)), sort_keys=True)
                input_missing.append(patterns[flags])
            invalid_bits = hour_bits(invalid[selected], ids)
            for aggregate in aggregates if position is None else ("instant",):
                data = np.where(valid, hourly[selected], 0)
                calm = np.zeros(data.shape[-1], dtype=bool)
                if aggregate == "mean_vector" or metric.startswith("WIND_DIRECTION"):
                    u, v = [
                        np.where(valid, arrays[p][selected], 0).sum(axis=0) / expected
                        for p in ("u10", "v10")
                    ]
                    output = np.degrees(np.arctan2(-u, -v)) % 360
                    calm = np.hypot(u, v) <= 1e-12
                elif aggregate in {"sum", "mean", "instant"}:
                    output = data.sum(axis=0) / (expected if aggregate == "mean" else 1)
                elif aggregate == "min":
                    output = np.where(valid, hourly[selected], np.inf).min(axis=0)
                else:
                    output = np.where(valid, hourly[selected], -np.inf).max(axis=0)
                output = np.where((counts == expected) & ~calm, output, np.nan)
                status = np.where(
                    counts == expected, "COMPLETE", np.where(counts > 0, "PARTIAL", "UNAVAILABLE")
                )
                status = np.where(calm & (counts == expected), "UNAVAILABLE", status)
                name = (
                    metric
                    if position is not None or aggregate == "sum"
                    else metric + "_" + aggregate.upper()
                )
                frame = pd.DataFrame(
                    dict(
                        DATE=day,
                        TIMEZONE="UTC",
                        H3_INDEX=cells,
                        METRIC=name,
                        VALUE=output[ids],
                        UNITS=units,
                        SOURCE="ERA5",
                        BACKEND="CDS",
                        STATUS=status[ids],
                        VALID_HOURS=counts[ids],
                        INVALID_HOURS=invalid_counts[ids],
                        INVALID_DERIVATION_BITS=invalid_bits,
                        EXPECTED_HOURS=expected,
                        COVERAGE_FRACTION=counts[ids] / expected,
                        INTERVAL_START_UTC=(
                            stamps[position] - pd.Timedelta(hours=int(accumulated))
                        ).isoformat()
                        if position is not None
                        else start.isoformat(),
                        INTERVAL_END_UTC=stamps[position].isoformat()
                        if position is not None
                        else (start + pd.Timedelta(days=1)).isoformat(),
                        VALID_TIME_UTC=stamps[position].isoformat()
                        if position is not None
                        else None,
                        SOURCE_GRID_HASH=cube["metadata"]["grid_hash"],
                        SOURCE_GRID_INDEX=ids,
                        SOURCE_LAT=coords[:, 0],
                        SOURCE_LON=coords[:, 1],
                        SOURCE_GRID_DISTANCE_M=distance,
                        SPATIAL_METHOD=spatial_method,
                        SOURCE_LINEAGE_ID=lineage_id,
                        INPUT_MISSING_BITS=input_missing,
                        VALUE_STATUS=np.where(
                            calm[ids] & (counts[ids] == expected),
                            "UNDEFINED_CALM_MEAN",
                            np.where(
                                invalid_counts[ids] > 0, "OUTSIDE_SUPPORTED_RH_DOMAIN", status[ids]
                            ),
                        ),
                        CONSOLIDATION_STATUS="contains_preliminary_ERA5T"
                        if any(d.get("selected_revision") == 5 for d in deps)
                        else "final"
                        if any(d.get("selected_revision") for d in deps)
                        else "unavailable",
                        SOURCE_RESOLUTION="CDS 0.25 degree distribution; approximately 31 km model information",
                        WIND_VECTOR_BASIS="earth_relative"
                        if metric.startswith(("U_WIND", "V_WIND", "WIND_"))
                        else None,
                        AVAILABLE_AT_UTC=None,
                        METHOD=METHOD,
                        SCHEMA=SCHEMA,
                    )
                )
                yield frame
