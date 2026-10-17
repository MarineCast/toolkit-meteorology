"""Native hourly sampling and strict UTC reductions for the coastal ERA5 product."""
import hashlib
import json
from pathlib import Path

import h3
import numpy as np
import pandas as pd

from .core.artifacts import atomic_write_json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

BASE = {
    "temperature": ("TEMPERATURE_2M_C", "degC", ("t2m",), ("mean", "min", "max")),
    "dewpoint": ("DEWPOINT_2M_C", "degC", ("d2m",), ("mean",)),
    "relative_humidity": ("RELATIVE_HUMIDITY_2M_PCT", "percent", ("t2m", "d2m"), ("mean",)),
    "cloud_cover": ("TOTAL_CLOUD_COVER_PCT", "percent", ("tcc",), ("mean",)),
    "pressure": ("MEAN_SEA_LEVEL_PRESSURE_HPA", "hPa", ("msl",), ("mean", "min")),
    "precipitation": ("PRECIPITATION_MM", "mm", ("tp",), ("sum",)),
}
WIND = (
    ("U_WIND_10M_MS", "m/s", ("u10",), ("mean",)),
    ("V_WIND_10M_MS", "m/s", ("v10",), ("mean",)),
    ("WIND_SPEED_10M_MS", "m/s", ("u10", "v10"), ("mean", "max")),
    ("WIND_DIRECTION_10M_DEG_FROM", "degrees", ("u10", "v10"), ("mean_vector",)),
)


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
    polygon = {'type': 'Polygon', 'coordinates': [[(w, s), (e, s), (e, n), (w, n), (w, s)]]}
    cells = sorted(cell for cell in h3.geo_to_cells(polygon, 5)
                   if s <= h3.cell_to_latlng(cell)[0] <= n and w <= h3.cell_to_latlng(cell)[1] <= e)
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
    atomic_write_json(path, record, overwrite=True)
    return np.array(nearest), cells, np.array(distances), identity["method"]


def day_frames(cube, boundary, day, request, spatial):
    """Emit one day/metric at a time, after temporal reduction on native points."""
    if boundary is not None and (
        boundary["metadata"]["grid_hash"] != cube["metadata"]["grid_hash"]
    ):
        raise ValueError("Boundary grid differs from the primary source grid.")
    ids, cells, _, _ = spatial
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
        arrays = {}
        for parameter in parameters:
            key = (parameter, accumulated)
            if key not in inputs:
                inputs[key] = series(cube, boundary, parameter, stamps)
            values, _ = inputs[key]
            arrays[parameter] = values
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
                yield pd.DataFrame(dict(
                    METRIC=name, VALUE=output[ids],
                    VALUE_STATUS=np.where(
                        calm[ids] & (counts[ids] == expected), 'UNDEFINED_CALM_MEAN',
                        np.where(invalid_counts[ids] > 0, 'OUTSIDE_SUPPORTED_RH_DOMAIN', status[ids])),
                ))
