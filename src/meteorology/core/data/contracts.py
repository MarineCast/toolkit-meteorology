from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pyarrow as pa


class DatasetId(str):
    """Validated logical identity for a durable dataset."""

    def __new__(cls, value: str):
        parts = value.split(".")
        if len(parts) < 3 or any(not part.replace("_", "").isalnum() for part in parts):
            raise ValueError(f"Invalid dataset id: {value!r}")
        return str.__new__(cls, value)


class DatasetLayer(StrEnum):
    SOURCE = "source"
    NORMALIZED = "normalized"
    DOMAIN = "domain"
    FEATURE = "feature"
    PUBLISHED = "published"


class DatasetFormat(StrEnum):
    PARQUET = "parquet"
    GEOPARQUET = "geoparquet"
    COG = "cog"
    JSON = "json"
    NPZ = "npz"
    DIRECTORY = "directory"


class ProcessingMode(StrEnum):
    RETROSPECTIVE = "retrospective"
    AS_OF = "as_of"


@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: DatasetId
    layer: DatasetLayer
    format: DatasetFormat
    path_template: str
    producer: str
    schema_version: str = "1"
    schema: pa.Schema | None = None
    primary_key: tuple[str, ...] = ()
    partition_keys: tuple[str, ...] = ()
    dependencies: tuple[DatasetId, ...] = ()
    allowed_modes: tuple[ProcessingMode, ...] = (
        ProcessingMode.RETROSPECTIVE,
        ProcessingMode.AS_OF,
    )
    sensitivity: str = "internal"

    def path(self, *, data_root: Path, artifact_root: Path, output_root: Path) -> Path:
        return Path(
            self.path_template.format(
                data_root=data_root, artifact_root=artifact_root, output_root=output_root
            )
        )
