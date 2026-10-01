"""Immutable acquisition objects and ownership for one mutable raw workspace."""

from __future__ import annotations

import fcntl
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pyarrow as pa

from ..artifacts import sha256_file, write_table


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publication_needs_recovery(parent: Path) -> bool:
    """A pending journal makes a two-file metadata view unsafe to read."""

    transactions = parent / ".transactions"
    return transactions.exists() and any(transactions.iterdir())


def resolve_raw_relative(raw_dir: Path, relative: str) -> Path:
    """Resolve an inventory reference without leaving its raw workspace."""

    value = Path(relative)
    root = raw_dir.resolve()
    if value.is_absolute() or not value.parts or ".." in value.parts:
        raise ValueError(f"Unsafe HRRR inventory relative path: {relative!r}")
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"HRRR inventory path escapes raw workspace: {relative!r}")
    return path


@contextmanager
def acquisition_lock(raw_dir: Path, *, writer: bool) -> Iterator[None]:
    """Own a raw workspace until canonical metadata or a read snapshot is stable.

    Writers fail busy rather than racing. Readers fail busy or require recovery;
    neither can accept a partly promoted inventory/manifest pair. Kernel locks
    release after process termination, including SIGKILL.
    """

    root = raw_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".acquisition.lock").open("a+b") as handle:
        operation = fcntl.LOCK_EX if writer else fcntl.LOCK_SH
        try:
            fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"HRRR acquisition workspace is busy: {root}") from exc
        try:
            if not writer and publication_needs_recovery(root):
                raise RuntimeError(
                    f"HRRR acquisition metadata requires recovery before reading: {root}"
                )
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_immutable_table(
    frame: pd.DataFrame,
    *,
    raw_dir: Path,
    family: str,
    schema: pa.Schema,
    validate: Callable[[Path], bool],
) -> tuple[Path, str]:
    """Validate and fsync a serialized Parquet object, then link it once by SHA-256.

    A partial temporary file is never referenced. Existing content addresses are
    verified and reused, never replaced. A process killed before the inventory
    checkpoint may leave an unreferenced object for a later explicit cleanup.
    """

    if family not in {"samples", "crosswalks"}:
        raise ValueError(f"Unknown acquisition object family: {family}")
    objects = raw_dir / "objects"
    parent = objects / family
    if objects.is_symlink() or parent.is_symlink():
        raise ValueError("Acquisition object directory cannot be a symlink.")
    parent.mkdir(parents=True, exist_ok=True)
    temporary = parent / f".{uuid.uuid4().hex}.part"
    try:
        write_table(temporary, pa.Table.from_pandas(frame, preserve_index=False), schema)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        if not validate(temporary):
            raise ValueError(f"Serialized HRRR {family} object failed validation: {temporary}")
        checksum = sha256_file(temporary)
        destination = parent / f"{checksum}.parquet"
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or not destination.is_file() or sha256_file(destination) != checksum:
                raise ValueError(f"Existing HRRR content address differs from its bytes: {destination}")
        _fsync_directory(parent)
        return destination, checksum
    finally:
        temporary.unlink(missing_ok=True)


def copy_referenced_object(
    *, source_root: Path, target_root: Path, relative: str, checksum: str
) -> Path:
    """Copy an inventory reference to a separate snapshot without replacing a target."""

    source = resolve_raw_relative(source_root, relative)
    target = resolve_raw_relative(target_root, relative)
    if sha256_file(source) != checksum:
        raise ValueError(f"Snapshot source checksum differs from inventory: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{uuid.uuid4().hex}.part"
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        if sha256_file(temporary) != checksum:
            raise ValueError(f"Snapshot copy checksum differs from inventory: {temporary}")
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or sha256_file(target) != checksum:
                raise ValueError(f"Existing snapshot object has conflicting bytes: {target}")
        _fsync_directory(target.parent)
        return target
    finally:
        temporary.unlink(missing_ok=True)
