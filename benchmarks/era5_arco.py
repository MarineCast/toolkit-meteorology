"""Bounded public ARCO transfer/representation probe; never a production backend.

Install numcodecs only for this optional benchmark. Default is a metadata plan;
--run reads the exact selected variables/hours subject to request/byte/time caps.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.request import urlopen

import numpy as np
import psutil

ROOT = (
    "https://storage.googleapis.com/gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
)
VARIABLES = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "mean_sea_level_pressure",
    "total_cloud_cover",
    "total_precipitation",
    "snowfall",
]


def probe(
    start,
    end,
    bbox,
    output,
    *,
    run=False,
    variables=None,
    sample_output=None,
    transfer_bytes=256 * 1024**2,
    max_requests=100,
    max_seconds=300,
):
    started = time.perf_counter()
    if sample_output is not None and not run:
        raise ValueError("Regional samples require explicit --run.")
    received, requests, peak, receipts = 0, 0, 0, []
    active = threading.Event()
    active.set()

    def monitor():
        nonlocal peak
        while active.wait(0.02) and active.is_set():
            peak = max(peak, psutil.Process().memory_info().rss)
            time.sleep(0.02)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()

    def fetch(relative, cap):
        nonlocal received, requests
        if requests + 1 > max_requests or time.perf_counter() - started >= max_seconds:
            raise ValueError("ARCO benchmark request/time cap reached; no support is widened.")
        requests += 1
        begin = time.perf_counter()
        with urlopen(
            ROOT + "/" + relative, timeout=min(30, max_seconds - (begin - started))
        ) as stream:
            length = stream.headers.get("Content-Length")
            if length is not None and (
                int(length) > cap or received + int(length) > transfer_bytes
            ):
                raise ValueError("ARCO chunk exceeds bounded benchmark transfer cap.")
            content = []
            total = 0
            while True:
                if time.perf_counter() - started >= max_seconds:
                    raise ValueError("ARCO benchmark time cap reached during transfer.")
                remaining = min(cap - total, transfer_bytes - received)
                if remaining <= 0:
                    raise ValueError("ARCO bounded read exhausted without proven EOF.")
                data = stream.read(min(65536, remaining))
                if not data:
                    break
                received += len(data)
                total += len(data)
                content.append(data)
        data = b"".join(content)
        receipts.append(
            dict(
                object=relative,
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                seconds=time.perf_counter() - begin,
            )
        )
        return data

    try:
        metadata_bytes = fetch(".zmetadata", 8 * 1024**2)
        metadata = json.loads(metadata_bytes)["metadata"]
        selected = variables or ["2m_temperature"]
        description = {
            v: dict(array=metadata[v + "/.zarray"], attrs=metadata[v + "/.zattrs"])
            for v in VARIABLES
        }
        report = dict(
            source=ROOT,
            collection=metadata[".zattrs"],
            variables=description,
            requested_variables=selected,
            bbox=bbox,
            start=start,
            end=end,
            scientific_equivalence="NOT_RUN_REQUIRES_CDS_NUMERICAL_AND_ACCUMULATION_COMPARISON",
            preferred_backend=False,
            reason="global spatial chunks and unqualified message-level provenance",
        )
        if run:
            import numcodecs

            def array(variable, chunk):
                spec = metadata[variable + "/.zarray"]
                expected = int(np.prod(spec["chunks"])) * np.dtype(spec["dtype"]).itemsize
                encoded = fetch(variable + "/" + chunk, expected + 1024**2)
                decoded = numcodecs.get_codec(spec["compressor"]).decode(encoded)
                if len(decoded) != expected:
                    raise ValueError(
                        "ARCO decompressed chunk shape differs from declared metadata."
                    )
                return np.frombuffer(decoded, dtype=spec["dtype"]).reshape(spec["chunks"])

            latitude = array("latitude", "0")
            longitude = array("longitude", "0")
            west, south, east, north = bbox
            longitude = (longitude + 180) % 360 - 180
            lat_ids = np.flatnonzero((latitude >= south) & (latitude <= north))
            lon_ids = np.flatnonzero((longitude >= west) & (longitude <= east))
            first = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
            last = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
            origin = datetime(1900, 1, 1, tzinfo=timezone.utc)
            positions = range(
                int((first - origin).total_seconds() / 3600),
                int((last - origin).total_seconds() / 3600) + 24,
            )
            time_spec = metadata["time/.zarray"]
            if metadata["time/.zattrs"]["units"] != "hours since 1900-01-01 00:00:00":
                raise ValueError("Unexpected ARCO time-coordinate representation.")
            times = {}
            samples = {v: [] for v in selected}
            decode_seconds = 0.0
            for index in positions:
                time_chunk = index // time_spec["chunks"][0]
                if time_chunk not in times:
                    times[time_chunk] = array("time", str(time_chunk))
                if int(times[time_chunk][index % time_spec["chunks"][0]]) != index:
                    raise ValueError("ARCO actual time coordinate differs from requested UTC hour.")
                for variable in selected:
                    before = time.perf_counter()
                    data = array(variable, f"{index}.0.0")
                    region = data[0][np.ix_(lat_ids, lon_ids)].copy()
                    decode_seconds += time.perf_counter() - before - receipts[-1]["seconds"]
                    samples[variable].append(region)
            report["regional_samples"] = {
                v: dict(
                    shape=list(np.array(values).shape),
                    minimum=float(np.nanmin(values)),
                    maximum=float(np.nanmax(values)),
                    mean=float(np.nanmean(values)),
                )
                for v, values in samples.items()
            }
            report["decode_crop_seconds"] = decode_seconds
            if sample_output is not None:
                target = Path(sample_output)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    np.savez_compressed(
                        stream,
                        **{v: np.array(values) for v, values in samples.items()},
                        latitude_deg=latitude[lat_ids],
                        longitude_deg=longitude[lon_ids],
                        time_hours_since_1900=np.array(list(positions)),
                    )
                report["regional_sample_artifact"] = dict(
                    path=str(target),
                    bytes=target.stat().st_size,
                    sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                    format="arco-region-samples-v1",
                )
        report.update(
            received_bytes=received,
            provider_requests=requests,
            peak_rss_bytes=peak,
            total_seconds=time.perf_counter() - started,
            receipts=receipts,
            scope="live_public_metadata_and_exact_samples" if run else "live_metadata_only",
        )
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError("Benchmark evidence is never overwritten.")
        output.write_text(json.dumps(report, indent=2))
        return report
    finally:
        active.clear()
        thread.join(timeout=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2024-01-02")
    parser.add_argument("--bbox", nargs=4, type=float, default=[-129.7, 45.9, -121.5, 51.5])
    parser.add_argument("--variables", nargs="+", choices=VARIABLES, default=["2m_temperature"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--sample-output", type=Path)
    args = parser.parse_args()
    result = probe(
        args.start_date,
        args.end_date,
        args.bbox,
        args.output,
        run=args.run,
        variables=args.variables,
        sample_output=args.sample_output,
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"receipts", "variables"}}, indent=2
        )
    )
