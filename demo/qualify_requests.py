"""Opt-in public-getter compatibility check. Default is an offline dry run.

Amount qualification requires a supplied, independently vetted historical case
and its retained reference bytes; this tool does not certify regional accuracy.
"""

import argparse
import hashlib
import json
import numpy as np
import pandas as pd
from pathlib import Path

from meteorology import get_weather_request as get_weather
from meteorology.era5.resources import Limits


def load_case(path, amounts):
    case = json.loads(path.read_text())
    if amounts:
        reference = case["independent_reference"]
        file = path.parent / reference["file"]
        if (
            hashlib.sha256(file.read_bytes()).hexdigest() != reference["sha256"]
            or not reference.get("uri")
            or not reference.get("verification_statement")
            or reference.get("verified_nonzero_parameters") != ["tp", "sf"]
            or reference.get("date") != case["date"]
            or reference.get("bbox") != case["bbox"]
        ):
            raise ValueError(
                "Supply matching, independently verified nonzero tp/sf reference evidence; never guess or widen the case."
            )
    return case


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", choices=["hrrr", "era5-temperature", "era5-amounts"], required=True
    )
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Explicit opt-in provider acquisition; otherwise offline planning.",
    )
    args = parser.parse_args(argv)
    amounts = args.source == "era5-amounts"
    case = load_case(args.case, amounts)
    # Exactly one UTC date and the supplied support. No alternate case or cap retry.
    variables = (
        ["temperature", "wind"]
        if args.source == "hrrr"
        else ["precipitation", "snowfall"]
        if amounts
        else ["temperature"]
    )
    source = "hrrr" if args.source == "hrrr" else "era5"
    limits = Limits(
        memory_bytes=1024**3,
        staging_bytes=2 * 1024**3,
        transfer_bytes=512 * 1024**2,
        requests=128 if source == "hrrr" else 32,
        seconds=1800,
    )
    options = dict(
        cache_dir=args.cache_dir, spatial_output="native", frequency="hourly", limits=limits
    )
    plan = get_weather(
        variables, case["date"], case["date"], case["bbox"], source, dry_run=True, **options
    )
    if not args.run:
        print(json.dumps(plan, indent=2))
        return 0
    result = get_weather(variables, case["date"], case["date"], case["bbox"], source, **options)
    data = result.to_pandas()
    if set(data.STATUS) != {"COMPLETE"} or set(data.TIMEZONE) != {"UTC"}:
        raise ValueError(
            "Incomplete source compatibility check; retain evidence and inspect the exact case."
        )
    if (
        not data.SOURCE_GRID_HASH.notna().all()
        or not data.SOURCE_SHA256_RECEIPTS.map(lambda value: bool(json.loads(value))).all()
    ):
        raise ValueError("Source support/receipts are absent.")
    evidence = [dep for value in data.SOURCE_DEPENDENCIES for dep in json.loads(value)]
    if any(
        dep["valid_time_utc"] != row_time
        for value, row_time in zip(data.SOURCE_DEPENDENCIES, data.VALID_TIME_UTC)
        for dep in json.loads(value)
    ):
        raise ValueError("UTC source/metric slots disagree.")
    if not all("init_time_utc" in dep and "forecast_hour" in dep for dep in evidence):
        raise ValueError("Forecast initialization/lead evidence is absent.")
    units = {
        "TEMPERATURE_2M_C": "degC",
        "U_WIND_10M_MS": "m/s",
        "V_WIND_10M_MS": "m/s",
        "WIND_SPEED_10M_MS": "m/s",
        "PRECIPITATION_MM": "mm",
        "SNOWFALL_WATER_EQUIVALENT_MM": "mm water equivalent",
    }
    start = pd.Timestamp(case["date"], tz="UTC")
    expected_slots = set(
        pd.date_range(start + pd.Timedelta(hours=1 if amounts else 0), periods=24, freq="h").map(
            lambda value: value.isoformat()
        )
    )
    for metric, rows in data.groupby("METRIC"):
        if (
            set(rows.VALID_TIME_UTC) != expected_slots
            or not (rows.groupby("SOURCE_GRID_INDEX").size() == 24).all()
        ):
            raise ValueError("Requested UTC-day slots/support are incomplete or duplicated.")
        times = pd.to_datetime(rows.VALID_TIME_UTC, utc=True)
        left = times - pd.Timedelta(hours=1) if amounts else times
        if (
            not (pd.to_datetime(rows.INTERVAL_START_UTC, utc=True) == left).all()
            or not (pd.to_datetime(rows.INTERVAL_END_UTC, utc=True) == times).all()
        ):
            raise ValueError("Requested UTC interval boundaries differ from metric support.")
        if set(rows.UNITS) != {units[metric]} or (amounts and not (rows.VALUE > 0).any()):
            raise ValueError(
                "Units/nonzero reference case check failed; do not change dates, support or caps automatically."
            )
    if args.source == "hrrr":
        wind = data[data.METRIC.isin(["U_WIND_10M_MS", "V_WIND_10M_MS", "WIND_SPEED_10M_MS"])]
        if set(wind.WIND_VECTOR_BASIS) != {"earth_relative"}:
            raise ValueError("HRRR vectors do not declare earth-relative support.")
        wide = wind.pivot(
            index=["SOURCE_GRID_INDEX", "VALID_TIME_UTC"], columns="METRIC", values="VALUE"
        )
        if not np.allclose(
            wide.WIND_SPEED_10M_MS, np.hypot(wide.U_WIND_10M_MS, wide.V_WIND_10M_MS)
        ):
            raise ValueError("HRRR vector/magnitude identity failed.")
    repeated = get_weather(variables, case["date"], case["date"], case["bbox"], source, **options)
    if not repeated.cache_hit or repeated.metadata["result_id"] != result.metadata["result_id"]:
        raise ValueError("Final result did not reuse the committed source vintage.")
    output = args.cache_dir.parent / (
        args.source + "-" + result.metadata["result_id"] + "-qualification.parquet"
    )
    if output.exists():
        raise FileExistsError(
            "Qualification export already exists; retained evidence is not overwritten."
        )
    result.to_parquet(output)
    print(
        json.dumps(
            dict(
                software_regression="separate_offline_suite",
                provider_compatibility="checked_for_exact_case",
                independent_regional_accuracy="NOT_RUN",
                metadata=result.metadata,
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
