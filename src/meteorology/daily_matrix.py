"""Combine native daily weather and astronomy without spatial resampling."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

import h3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .artifacts import load_manifest, resolve_portable_path, checksum_path, code_state, stable_hash
from .core.artifacts import TransactionalFamilyPublisher, atomic_write_json
from ._version import __version__
from .methods import method_version

KEYS = ["DATE", "H3_INDEX", "H3_RESOLUTION"]
RESOLUTIONS = {"surface_weather": 5, "daylight": 4, "lunar": 5}


def units(column: str) -> str:
    if column.startswith("TEMPERATURE"):
        return "degrees Celsius"
    if column.startswith("WIND_DIRECTION"):
        return "degrees clockwise from north (FROM)"
    if "WIND" in column:
        return "metres per second"
    if column.startswith("VISIBILITY"):
        return "kilometres"
    if column.startswith("PRECIP_MM"):
        return "millimetres (six-snapshot estimate)"
    if "PRESSURE_HPA" in column:
        return "hectopascals"
    if column.endswith("_PCT") or "_PCT_" in column:
        return "percent"
    if "DISTANCE_M" in column:
        return "metres"
    if column.endswith("_HOURS") or column == "NIGHT_HOURS":
        return "hours"
    if column.endswith("_DEG"):
        return "degrees"
    if column.endswith("_DAYS"):
        return "days"
    if "FRACTION" in column or "WEIGHT" in column or column.endswith("_FRAC"):
        return "dimensionless"
    if column.endswith("_UTC"):
        return "UTC timestamp"
    if column.endswith("_LAT") or column.endswith("_LON"):
        return "degrees WGS84"
    return "label, count or calendar/QC metadata"


def combine_frames(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    """Outer join at native resolution; preserve source fields and missing support."""
    if not frames:
        raise ValueError("No daily components supplied.")
    output, fields, date_set = None, {}, None
    for component, source in frames.items():
        if component not in RESOLUTIONS:
            raise ValueError(f"Unsupported component: {component}")
        frame = source.copy()
        if not {"DATE", "H3_INDEX"}.issubset(frame):
            raise ValueError("Missing date/H3 identity")
        if (
            frame.empty
            or frame[["DATE", "H3_INDEX"]].isna().any().any()
            or frame.duplicated(["DATE", "H3_INDEX"]).any()
        ):
            raise ValueError("Empty, null or duplicate date/H3 identity")
        resolution = RESOLUTIONS[component]
        cells = frame.H3_INDEX.unique()
        if any(not h3.is_valid_cell(c) or h3.get_resolution(c) != resolution for c in cells):
            raise ValueError(f"{component}: invalid native H3 resolution")
        if "H3_RESOLUTION" in frame and not frame.H3_RESOLUTION.eq(resolution).all():
            raise ValueError("H3 resolution column mismatch")
        frame["H3_RESOLUTION"] = resolution
        dates = set(frame.DATE)
        if any(pd.Timestamp(d).strftime("%Y-%m-%d") != d for d in dates):
            raise ValueError("Invalid local date")
        if date_set is not None and dates != date_set:
            raise ValueError("Component date coverage differs")
        date_set = dates
        if not frame.groupby("DATE").size().eq(len(cells)).all():
            raise ValueError("Incomplete daily support")
        names = {}
        for column in frame.columns:
            if column in KEYS:
                continue
            name = f"{component}__{column}"
            names[column] = name
            fields[name] = {
                "component": component,
                "variable": column,
                "unit": units(column),
                "native_resolution": resolution,
            }
        frame = frame.rename(columns=names)
        output = (
            frame
            if output is None
            else output.merge(frame, on=KEYS, how="outer", validate="one_to_one")
        )
    return output.sort_values(KEYS).reset_index(drop=True), fields


def export(manifest_paths: list[Path], output: Path) -> Path:
    """Verify complete native manifests, export one Parquet with yearly row groups."""
    if output.exists():
        raise FileExistsError(output)
    manifests, roots, coverage, timezone = {}, {}, None, None
    for path in manifest_paths:
        manifest = load_manifest(path, verify_artifacts=True)
        component = manifest["product"].removeprefix("meteorological.")
        if component not in RESOLUTIONS or component in manifests:
            raise ValueError("Unsupported or repeated manifest")
        current = manifest["temporal_coverage"]
        zone = manifest["resolved_config"]["timezone"]
        if coverage is not None and (current != coverage or zone != timezone):
            raise ValueError("Date range or timezone mismatch")
        coverage, timezone = current, zone
        candidates = [
            resolve_portable_path(a["path"], base=path.parent)
            for a in manifest["artifacts"]
            if "DAILY" in Path(a["path"]).name
        ]
        if len(candidates) != 1:
            raise ValueError("Daily artifact is not uniquely declared")
        roots[component] = candidates[0]
        manifests[component] = {
            "path": str(path.resolve()),
            "checksum": checksum_path(path),
            "manifest": manifest,
        }
    if set(manifests) != set(RESOLUTIONS):
        raise ValueError("Weather, daylight and lunar manifests are required")
    expected = pd.date_range(coverage["start_date"], coverage["end_date"]).strftime("%Y-%m-%d")
    years = sorted({d[:4] for d in expected})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    sidecar = output.with_suffix(output.suffix + ".manifest.json")
    temporary_sidecar = temporary.with_suffix(temporary.suffix + ".manifest.json")
    writer = None
    try:
        for year in years:
            frames = {}
            for component, root in roots.items():
                files = sorted((root / f"year={year}").rglob("*.parquet"))
                if not files:
                    raise ValueError(f"No {component} data for {year}")
                frames[component] = pa.concat_tables(
                    [pq.ParquetFile(f).read() for f in files]
                ).to_pandas()
            frame, fields = combine_frames(frames)
            if set(frame.DATE) != {d for d in expected if d.startswith(year)}:
                raise ValueError("Dates do not match manifest coverage")
            # An Arrow schema from native tables preserves types when outer joins introduce nulls.
            schema_fields = [
                pa.field("DATE", pa.string()),
                pa.field("H3_INDEX", pa.string()),
                pa.field("H3_RESOLUTION", pa.int8()),
            ]
            for component, root in roots.items():
                native = pq.ParquetFile(
                    sorted((root / f"year={year}").rglob("*.parquet"))[0]
                ).schema_arrow
                schema_fields += [
                    pa.field(f"{component}__{f.name}", f.type) for f in native if f.name not in KEYS
                ]
            metadata = {
                "schema_version": 2,
                "software_version": __version__,
                "method_version": method_version("meteorological.daily_matrix"),
                "release_id": stable_hash(
                    {
                        "method": method_version("meteorological.daily_matrix"),
                        "software": __version__,
                        "source_releases": {
                            name: value["manifest"].get("release_id", value["checksum"])
                            for name, value in manifests.items()
                        },
                        "timezone": timezone,
                        "coverage": coverage,
                    }
                ),
                "fields": fields,
                "native_manifests": manifests,
                "temporal_coverage": coverage,
                "timezone": timezone,
                "code_state": code_state(),
                "limitations": [
                    "Native R4 daylight and R5 weather/lunar; no resampling or marine-only filtering.",
                    "Nulls indicate unsupported component resolution; no zero filling.",
                    "HRRR f00 core analyses and matched f01 precipitation rates remain distinct.",
                    "Precipitation is a six-snapshot daily estimate, not a true 24-hour accumulation.",
                    "Astronomy uses approximate deterministic calculations; weather is retrospective context, not occurrence or detection probability.",
                ],
            }
            schema = pa.schema(
                schema_fields,
                metadata={
                    b"meteorology_daily_matrix": json.dumps(metadata, sort_keys=True).encode()
                },
            )
            table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temporary, schema, compression="zstd")
            writer.write_table(table, row_group_size=50000)
            print(f"Exported {year}: {len(frame):,} rows")
        writer.close()
        writer = None
        atomic_write_json(
            temporary_sidecar,
            {
                "schema_version": 1,
                "release_id": metadata["release_id"],
                "matrix_checksum": checksum_path(temporary),
            },
            overwrite=False,
        )
        from .validation import validate_daily_matrix

        validate_daily_matrix(temporary)
        with TransactionalFamilyPublisher(output.parent) as publisher:
            if output.exists() or sidecar.exists():
                raise FileExistsError(output if output.exists() else sidecar)
            staged = publisher.stage_path(output)
            staged_sidecar = publisher.stage_manifest_path(sidecar)
            os.replace(temporary, staged)
            os.replace(temporary_sidecar, staged_sidecar)
            publisher.publish()
    finally:
        if writer is not None:
            writer.close()
        if temporary.exists():
            temporary.unlink()
        temporary_sidecar.unlink(missing_ok=True)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(export(args.manifest, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
