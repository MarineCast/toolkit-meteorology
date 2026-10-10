"""Strict ERA5 single-level GRIB message normalization, with explicit expver lineage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..artifacts import checksum_path
from ..temporal_products import _utc
from .planning import DATASET

# CDS single-level reanalysis, not ERA5-Land nor cumulative MARS forecasts.
PARAMETERS = {
    167: ("t2m", "K"),
    168: ("d2m", "K"),
    165: ("u10", "m s**-1"),
    166: ("v10", "m s**-1"),
    151: ("msl", "Pa"),
    164: ("tcc", "(0 - 1)"),
    228: ("tp", "m"),
}
NORMALIZATION_METHOD = "era5-cds-regular-latlon-message-v1"


def normalize_message(metadata: dict, latitude, longitude, values, *, budget=None) -> pd.DataFrame:
    """Validate decoded metadata before conversion; missing values stay missing."""
    if metadata.get("dataset") != DATASET or metadata.get("product_type") != "reanalysis":
        raise ValueError("Expected CDS ERA5 single-level reanalysis, not a substitute source.")
    if (
        metadata.get("centre") != "ecmf"
        or metadata.get("mars_class") != "ea"
        or metadata.get("mars_stream") != "oper"
    ):
        raise ValueError(
            "GRIB originating centre/class/stream is not ERA5 deterministic reanalysis."
        )
    param = metadata.get("paramId")
    if param not in PARAMETERS:
        raise ValueError("Unsupported ERA5 parameter.")
    name, units = PARAMETERS[param]
    valid, retrieved = _utc(metadata["valid_time_utc"]), _utc(metadata["retrieved_at_utc"])
    if valid.minute or valid.second or valid.microsecond or retrieved < valid:
        raise ValueError("ERA5 requires whole-hour UTC valid times and retrospective retrieval.")
    expver = int(metadata["expver"])
    if expver not in (1, 5):
        raise ValueError(
            "Only explicit ERA5 final expver=1 or preliminary ERA5T expver=5 is supported."
        )
    if metadata.get("gridType") != "regular_ll" or any(
        abs(float(metadata.get(k, 0)) - 0.25) > 1e-9
        for k in ("iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees")
    ):
        raise ValueError("Expected CDS regular 0.25 degree distribution grid.")
    accepted_units = {units}
    if name == "tcc":
        accepted_units |= {"1", "~"}
    if str(metadata.get("units")) not in accepted_units:
        raise ValueError(f"{name}: unexpected source units; expected {units}.")
    if name in ("u10", "v10") and metadata.get("uvRelativeToGrid") != 0:
        raise ValueError("ERA5 wind must be verified earth-relative.")
    if metadata.get("data_type") != ("fc" if name == "tp" else "an"):
        raise ValueError("Unexpected ERA5 analysis/forecast parameter provenance.")
    if name == "tp":
        if (
            metadata.get("stepType") != "accum"
            or metadata.get("step_units") != "hours"
            or type(metadata.get("startStep")) is not int
            or type(metadata.get("endStep")) is not int
            or metadata["startStep"] < 0
            or metadata["endStep"] - metadata["startStep"] != 1
        ):
            raise ValueError(
                "CDS tp must be one-hour accumulation, not cumulative ERA5-Land/MARS or f00 zero."
            )
        init = _utc(metadata["init_time_utc"])
        if init + timedelta(hours=metadata["endStep"]) != valid:
            raise ValueError("Precipitation step and initialization disagree with valid time.")
        left = valid - timedelta(hours=1)
    else:
        if metadata.get("stepType") != "instant":
            raise ValueError("Atmospheric ERA5 parameter must be instantaneous.")
        if _utc(metadata["init_time_utc"]) != valid or metadata.get("endStep") != 0:
            raise ValueError("Instantaneous ERA5 analysis must have its own zero-step valid time.")
        left = valid
    if budget is not None:
        from .resources import ROW_BYTES, COPIES

        budget.checkpoint(additional_memory=np.size(values) * ROW_BYTES * COPIES)
    lat, lon, data = [np.asarray(v, dtype="float64").ravel() for v in (latitude, longitude, values)]
    lon = (lon + 180) % 360 - 180
    if len(data) == 0 or not (len(lat) == len(lon) == len(data)):
        raise ValueError("Source coordinate/value shapes disagree or are empty.")
    if not (np.isfinite(lat).all() and np.isfinite(lon).all()) or np.any(abs(lat) > 90):
        raise ValueError("Invalid source coordinates.")
    order = np.lexsort((lon, lat))
    lat, lon, data = lat[order], lon[order], data[order]
    if len(set(zip(lat, lon))) != len(data):
        raise ValueError("Duplicate source grid point.")
    if len(np.unique(lat)) * len(np.unique(lon)) != len(data):
        raise ValueError("Regular grid rectangle has missing source coordinates.")
    for axis in (np.unique(lat), np.unique(lon)):
        if len(axis) > 1 and not np.allclose(np.diff(axis), 0.25, rtol=0, atol=1e-8):
            raise ValueError("Coordinates disagree with declared 0.25 degree grid.")
    if np.isinf(data).any():
        raise ValueError("Infinite source value; bitmap missingness must decode as NaN.")
    good = data[np.isfinite(data)]
    if (
        (name in ("t2m", "d2m") and np.any((good < 150) | (good > 350)))
        or (name == "msl" and np.any((good < 70000) | (good > 120000)))
        or (name == "tcc" and np.any((good < 0) | (good > 1)))
        or (name == "tp" and np.any(good < 0))
    ):
        raise ValueError(f"{name}: decoded value outside physical contract.")
    if not isinstance(metadata.get("source_sha256"), str) or not re.fullmatch(
        "[0-9a-f]{64}", metadata["source_sha256"]
    ):
        raise ValueError("Source-file SHA256 receipt is required.")
    coords = np.column_stack((lat, lon)).astype("<f8").tobytes()
    grid_hash = hashlib.sha256(coords).hexdigest()
    frame = pd.DataFrame(
        dict(
            SOURCE_GRID_INDEX=np.arange(len(data)),
            SOURCE_LAT=lat,
            SOURCE_LON=lon,
            SOURCE_GRID_HASH=grid_hash,
            SOURCE_GRID_SIZE=len(data),
            VALID_TIME_UTC=valid.isoformat(),
            INTERVAL_START_UTC=left.isoformat(),
            PARAMETER=name,
            VALUE=data,
            EXPVER=expver,
            CONSOLIDATION="final" if expver == 1 else "preliminary_ERA5T",
            RETRIEVED_AT_UTC=retrieved.isoformat(),
            SOURCE_SHA256=metadata["source_sha256"],
            SOURCE_DATASET=DATASET,
            SOURCE_UNITS=units,
            NORMALIZATION_METHOD=NORMALIZATION_METHOD,
        )
    )
    if budget is not None:
        budget.checkpoint()
    return frame


def consolidate(
    records: pd.DataFrame, *, as_of_utc: str | None = None, expected_native_indices=None
) -> pd.DataFrame:
    """Within one captured release prefer final 1 over 5; never overwrite old releases."""
    if records.empty:
        return records.copy()
    if set(records.SOURCE_DATASET) != {DATASET} or set(records.NORMALIZATION_METHOD) != {
        NORMALIZATION_METHOD
    }:
        raise ValueError("Mixed or unrecognized ERA5 source records.")
    if len(set(records.SOURCE_GRID_HASH)) != 1:
        raise ValueError("A source chunk cannot mix grids.")
    if records.SOURCE_GRID_SIZE.nunique() != 1 or set(records.SOURCE_GRID_INDEX) != (
        set(expected_native_indices)
        if expected_native_indices is not None
        else set(range(int(records.SOURCE_GRID_SIZE.iloc[0])))
    ):
        raise ValueError("Native source grid point universe is incomplete.")
    if not set(records.EXPVER) <= {1, 5}:
        raise ValueError("Unrecognized expver in normalized records.")
    keys = ["VALID_TIME_UTC", "SOURCE_GRID_INDEX", "PARAMETER"]
    if records.duplicated(keys + ["EXPVER"]).any():
        raise ValueError(
            "Duplicate same-expver time/point/parameter requires explicit source adjudication."
        )
    if as_of_utc is not None:
        as_of = _utc(as_of_utc)
        if any(_utc(t) > as_of for t in records.RETRIEVED_AT_UTC):
            raise ValueError(
                "Captured source was retrieved after requested as-of; historical availability is unknown."
            )
    # A missing final value does not silently borrow a preliminary value.
    return (
        records.sort_values("EXPVER")
        .drop_duplicates(keys, keep="first")
        .sort_values(keys)
        .reset_index(drop=True)
    )


def decode_grib(path: str | Path, *, retrieved_at_utc: str, budget=None):
    """Read one message at a time; ecCodes is an optional isolated acquisition extra."""
    import eccodes as ec

    path = Path(path)
    if budget is not None:
        budget.checkpoint(additional_memory=path.stat().st_size)
    sha = checksum_path(path)
    count = 0
    with path.open("rb") as handle:
        while (message := ec.codes_grib_new_from_file(handle)) is not None:
            try:

                def get(key):
                    return ec.codes_get(message, key)

                def stamp(d, t):
                    return (
                        datetime.strptime(f"{int(d):08}{int(t):04}", "%Y%m%d%H%M")
                        .replace(tzinfo=timezone.utc)
                        .isoformat()
                    )

                meta = {
                    key: get(key)
                    for key in (
                        "paramId",
                        "units",
                        "stepType",
                        "gridType",
                        "iDirectionIncrementInDegrees",
                        "jDirectionIncrementInDegrees",
                        "startStep",
                        "endStep",
                    )
                }
                meta.update(
                    dataset=DATASET,
                    product_type="reanalysis",
                    centre=get("centre"),
                    mars_class=get("class"),
                    mars_stream=get("stream"),
                    data_type=get("type"),
                    expver=get("experimentVersionNumber"),
                    valid_time_utc=stamp(get("validityDate"), get("validityTime")),
                    init_time_utc=stamp(get("dataDate"), get("dataTime")),
                    retrieved_at_utc=retrieved_at_utc,
                    source_sha256=sha,
                    step_units="hours" if get("stepUnits") == 1 else "unsupported",
                )
                if meta["paramId"] in (165, 166):
                    meta["uvRelativeToGrid"] = get("uvRelativeToGrid")
                if budget is not None:
                    from .resources import ROW_BYTES, COPIES

                    budget.checkpoint(
                        additional_memory=int(get("numberOfDataPoints")) * ROW_BYTES * COPIES
                    )
                values = np.asarray(ec.codes_get_values(message), dtype="float64")
                if get("bitmapPresent"):
                    bitmap = np.asarray(ec.codes_get_array(message, "bitmap"))
                    values = np.where(bitmap == 0, np.nan, values)
                count += 1
                yield normalize_message(
                    meta,
                    ec.codes_get_array(message, "latitudes"),
                    ec.codes_get_array(message, "longitudes"),
                    values,
                    budget=budget,
                )
            finally:
                ec.codes_release(message)
    if not count:
        raise ValueError("GRIB source contains no messages.")
