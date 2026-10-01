"""Immutable acquisition objects and ownership for one mutable raw workspace."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pandas as pd
import pyarrow as pa

from ..artifacts import (
    checksum_path, load_manifest, resolve_portable_path, sha256_file, stable_hash,
    write_manifest, write_table,
)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    """Make copied snapshot bytes and directory entries durable before promotion."""

    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        elif path.is_dir():
            _fsync_directory(path)
    _fsync_directory(root)


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


@contextmanager
def acquisition_read_locks(*roots: Path) -> Iterator[None]:
    """Read several acquisition publication roots in deterministic order."""

    with ExitStack() as stack:
        for root in sorted({path.resolve() for path in roots}):
            stack.enter_context(acquisition_lock(root, writer=False))
        yield


def inventory_raw_root(inventory_path: Path) -> Path:
    """Find object storage beside a canonical or immutable metadata inventory."""

    parent = inventory_path.resolve().parent
    if parent.parent.name == "snapshots" and len(parent.name) == 64:
        try:
            int(parent.name, 16)
        except ValueError:
            pass
        else:
            return parent.parent.parent
    return parent


def snapshot_acquisition_metadata(
    *, raw_dir: Path, inventory_path: Path, manifest_path: Path, manifest: dict,
    pinned_inputs: dict[Path, Path] | None = None,
) -> tuple[Path, Path]:
    """Pin the verified metadata bytes consumed by a weather build.

    The caller holds the acquisition reader locks. Referenced sample objects are
    already immutable and therefore need no duplicate copies here.
    """

    inventory_path = inventory_path.resolve()
    manifest_path = manifest_path.resolve()
    inventory_checksum = checksum_path(inventory_path)
    if len(manifest["artifacts"]) != 1 or manifest["artifacts"][0]["checksum"] != inventory_checksum:
        raise ValueError("Acquisition manifest does not describe the consumed inventory.")
    key = stable_hash({
        "inventory": inventory_checksum,
        "manifest": checksum_path(manifest_path),
        "pinned_inputs": {str(source): checksum_path(target) for source, target in (pinned_inputs or {}).items()},
    })
    snapshots = raw_dir.resolve() / "snapshots"
    if snapshots.is_symlink():
        raise ValueError("Acquisition snapshot directory cannot be a symlink.")
    snapshots.mkdir(parents=True, exist_ok=True)
    destination = snapshots / key
    frozen_inventory = destination / inventory_path.name
    frozen_manifest = destination / manifest_path.name
    if not destination.exists():
        temporary = snapshots / f".{uuid.uuid4().hex}.part"
        temporary.mkdir()
        try:
            inventory_copy = temporary / inventory_path.name
            shutil.copy2(inventory_path, inventory_copy)
            if checksum_path(inventory_copy) != inventory_checksum:
                raise ValueError("Acquisition inventory changed while taking its snapshot.")
            payload = json.loads(json.dumps(manifest))
            payload["artifacts"][0]["path"] = str(frozen_inventory)
            for item in payload["inputs"]:
                source = resolve_portable_path(item["path"], base=manifest_path.parent).resolve()
                if pinned_inputs and source in pinned_inputs:
                    item["path"] = str(pinned_inputs[source])
            contract = payload["artifacts"][0]
            contract["contract_hash"] = stable_hash(
                {field: value for field, value in contract.items() if field != "contract_hash"}
            )
            write_manifest(temporary / manifest_path.name, payload)
            _fsync_tree(temporary)
            try:
                temporary.rename(destination)
            except OSError:
                if not destination.exists():
                    raise
            _fsync_directory(snapshots)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    if checksum_path(frozen_inventory) != inventory_checksum:
        raise ValueError("Existing acquisition snapshot inventory has changed.")
    load_manifest(frozen_manifest, verify_artifacts=True)
    return frozen_inventory, frozen_manifest


def snapshot_support_inputs(
    *, raw_dir: Path, support_path: Path, support_manifest_path: Path
) -> tuple[Path, Path]:
    """Retain the exact support artifact and metadata read by a weather build."""

    support_path = support_path.resolve()
    support_manifest_path = support_manifest_path.resolve()
    checksums = {path: checksum_path(path) for path in (support_path, support_manifest_path)}
    key = stable_hash({str(path): value for path, value in checksums.items()})
    snapshots = raw_dir.resolve() / "support_snapshots"
    if snapshots.is_symlink():
        raise ValueError("Support snapshot directory cannot be a symlink.")
    snapshots.mkdir(parents=True, exist_ok=True)
    destination = snapshots / key
    if not destination.exists():
        temporary = snapshots / f".{uuid.uuid4().hex}.part"
        temporary.mkdir()
        try:
            for source, checksum in checksums.items():
                target = temporary / source.name
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
                if checksum_path(target) != checksum:
                    raise ValueError(f"Support changed while taking its snapshot: {source}")
            _fsync_tree(temporary)
            try:
                temporary.rename(destination)
            except OSError:
                if not destination.exists():
                    raise
            _fsync_directory(snapshots)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    for source, checksum in checksums.items():
        if checksum_path(destination / source.name) != checksum:
            raise ValueError(f"Existing support snapshot has changed: {source}")
    return destination / support_path.name, destination / support_manifest_path.name


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
