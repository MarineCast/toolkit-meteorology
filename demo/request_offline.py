"""Small generated ERA5 request example; zero provider calls, no credentials."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from meteorology import get_weather_request as get_weather


def main():
    import eccodes as ec

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / "synthetic-era5.grib"
    # Exclusive fixture creation preserves prior runs. Reuse the cache through the
    # public API independently; a demo rerun uses a fresh output directory.
    with source.open("xb") as handle:
        for hour in range(25):
            valid = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=hour)
            for parameter, value in ((167, 283.15), (144, 0.001)):
                if hour == 24 and parameter == 167:
                    continue
                accumulation = parameter == 144
                init = valid - pd.Timedelta(hours=1) if accumulation else valid
                m = ec.codes_grib_new_from_samples("regular_ll_sfc_grib1")
                try:
                    for key, v in {
                        "class": "ea",
                        "type": "fc" if accumulation else "an",
                        "paramId": parameter,
                        "Ni": 4,
                        "Nj": 4,
                        "latitudeOfFirstGridPointInDegrees": 49.5,
                        "latitudeOfLastGridPointInDegrees": 48.75,
                        "longitudeOfFirstGridPointInDegrees": 234.5,
                        "longitudeOfLastGridPointInDegrees": 235.25,
                        "iDirectionIncrementInDegrees": 0.25,
                        "jDirectionIncrementInDegrees": 0.25,
                        "dataDate": int(init.strftime("%Y%m%d")),
                        "dataTime": int(init.strftime("%H%M")),
                        "stepType": "accum" if accumulation else "instant",
                        "startStep": 0,
                        "endStep": 1 if accumulation else 0,
                    }.items():
                        ec.codes_set(m, key, v)
                    ec.codes_set_values(m, [value] * 16)
                    ec.codes_write(m, handle)
                finally:
                    ec.codes_release(m)
    options = dict(
        cache_dir=args.output / "cache",
        source_files=[source],
        retrieved_at_utc="2025-01-01T00:00:00Z",
    )
    result = get_weather(
        ["temperature", "snowfall"],
        "2024-01-01",
        "2024-01-01",
        (-125.25, 49, -125, 49.25),
        "era5",
        **options,
    )
    result.to_parquet(args.output / "synthetic-weather.parquet")
    print(
        result.to_pandas()[
            ["DATE", "SOURCE_GRID_INDEX", "METRIC", "VALUE", "UNITS", "STATUS"]
        ].to_string(index=False)
    )
    repeat = get_weather(
        ["snowfall", "temperature"],
        "2024-01-01",
        "2024-01-01",
        (-125.25, 49, -125, 49.25),
        "era5",
        **options,
    )
    print(
        f"SYNTHETIC fixture; provider calls: {result.metadata['budget']['requests']}; repeat cache hit: {repeat.cache_hit}"
    )


if __name__ == "__main__":
    main()
