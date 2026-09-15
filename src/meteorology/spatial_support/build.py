"""Build canonical full-bbox H3 support for meteorological products."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import box

from meteorology.core.artifacts import TransactionalFamilyPublisher
from meteorology.core.data.meteorological_schemas import SUPPORT_SCHEMA
from meteorology.core.geo.h3 import cell_to_latlng, polygon_to_cells

from ..artifacts import (
    cell_set_hash,
    load_manifest,
    manifest_payload,
    parquet_contract,
    portable_path,
    write_manifest,
    write_table,
)
from ..config import DEFAULT_CONFIG_PATH, load_meteorological_config


def _support_frame(bbox: dict[str, float], resolution: int) -> pd.DataFrame:
    geometry = box(bbox["min_lon"], bbox["min_lat"], bbox["max_lon"], bbox["max_lat"])
    cells = sorted(polygon_to_cells(geometry, int(resolution)))
    rows = []
    for cell in cells:
        lat, lon = cell_to_latlng(cell)
        if bbox["min_lat"] <= lat <= bbox["max_lat"] and bbox["min_lon"] <= lon <= bbox["max_lon"]:
            rows.append(
                {
                    "H3_INDEX": cell,
                    "H3_RESOLUTION": int(resolution),
                    "CENTROID_LAT": lat,
                    "CENTROID_LON": lon,
                    "SUPPORT_STATE": "MODEL_BBOX_CENTROID",
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError(f"Meteorological H3 support is empty at resolution {resolution}.")
    if frame["H3_INDEX"].duplicated().any():
        raise ValueError(f"Meteorological H3 support contains duplicates at R{resolution}.")
    return frame.sort_values("H3_INDEX").reset_index(drop=True)


def load_meteorological_support(
    resolution: int,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> pd.DataFrame:
    config = load_meteorological_config(config_path)
    path = config.support_path(int(resolution))
    if not path.exists():
        raise FileNotFoundError(
            f"Meteorological support R{resolution} is missing: {path}. Run the support builder."
        )
    manifest = load_manifest(config.support_manifest_path, verify_artifacts=True)
    expected_path = portable_path(path)
    contracts = [
        artifact for artifact in manifest["artifacts"] if artifact.get("path") == expected_path
    ]
    if len(contracts) != 1:
        raise ValueError(
            f"Meteorological support manifest does not uniquely declare R{resolution}: {path}"
        )
    frame = pq.read_table(path, schema=SUPPORT_SCHEMA).to_pandas()
    if frame.empty or frame["H3_INDEX"].duplicated().any():
        raise ValueError(f"Invalid meteorological support artifact: {path}")
    if set(frame["H3_RESOLUTION"].astype(int)) != {int(resolution)}:
        raise ValueError(f"Meteorological support has an invalid H3 resolution: {path}")
    if cell_set_hash(frame["H3_INDEX"]) != contracts[0]["h3_cell_set_hash"]:
        raise ValueError(f"Meteorological support membership does not match its manifest: {path}")
    return frame.sort_values("H3_INDEX").reset_index(drop=True)


def build_meteorological_spatial_support(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    run_id: str | None = None,
) -> tuple[Path, ...]:
    config = load_meteorological_config(config_path)
    run_id = run_id or f"meteorological-support-{uuid.uuid4().hex[:12]}"
    frames = {
        resolution: _support_frame(config.bbox, resolution)
        for resolution in config.support_resolutions
    }
    parent = config.support_output_dir.parent
    with TransactionalFamilyPublisher(parent, run_id=run_id) as publisher:
        contracts = []
        outputs = []
        for resolution, frame in frames.items():
            destination = config.support_path(resolution)
            staged = publisher.stage_path(destination)
            write_table(staged, pa.Table.from_pandas(frame, preserve_index=False), SUPPORT_SCHEMA)
            contract = parquet_contract(staged, published_path=destination)
            contracts.append(contract)
            outputs.append(destination)
        staged_manifest = publisher.stage_path(config.support_manifest_path)
        payload = manifest_payload(
            product="meteorological.spatial_support",
            run_id=run_id,
            config_path=config.path,
            resolved_config={
                "bbox": config.bbox,
                "resolutions": config.support_resolutions,
                "output_dir": str(config.support_output_dir),
            },
            artifacts=contracts,
            h3_resolution=None,
            spatial_bounds=config.bbox,
            source_completeness="not_applicable_deterministic",
            availability_semantics="Timeless deterministic support; available after a successful local build.",
            sources=[
                {
                    "name": "OrcaCast common model-area bounding box",
                    "license": "Internal configuration",
                    "attribution": "OrcaCast",
                    "observation_period": "Not applicable",
                    "redistribution_restrictions": "None",
                }
            ],
            limitations=[
                "Support includes every H3 centroid in the configured bounding box, including land and water."
            ],
        )
        write_manifest(staged_manifest, payload)
        publisher.publish()
    return tuple([*outputs, config.support_manifest_path])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    for path in build_meteorological_spatial_support(args.config, run_id=args.run_id):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
