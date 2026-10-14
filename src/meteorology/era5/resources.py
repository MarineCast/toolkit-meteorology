"""Cooperative resource accounting for bounded ERA5 scratch processing.

Preallocation estimates and chunk sizes bound planned work. RSS/time checks stop
at checkpoints, not inside pandas/ecCodes native allocations: no OS hard-memory
or wall-clock preemption is claimed. Transfer bytes are capped while streaming.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import resource
import sys
import time

import psutil

MIB = 1024**2
ROW_BYTES = 2048  # arrays/index + bounded identity strings/nullable values/Python overhead
COPIES = 4  # concat, selection/consolidation/sort, group/pivot and output overlap
BASELINE_BYTES = 128 * MIB
POINT_BATCH = 64
PARQUET_BATCH = 4096


def memory_estimate(
    points: int,
    *,
    point_batch: int = POINT_BATCH,
    source_bytes: int = 128 * MIB,
    records_per_point: int = 2 * (6 * 24 + 25),
) -> dict:
    if type(points) is not int or points <= 0:
        raise ValueError("Positive native grid size required.")
    # Two expvers, six instantaneous parameters * 24 hours and 25 tp slots.
    if type(records_per_point) is not int or records_per_point <= 0:
        raise ValueError("Positive per-point record allowance required.")
    normalized_day = points * records_per_point * ROW_BYTES
    aggregate = min(points, point_batch) * records_per_point * ROW_BYTES * COPIES
    parquet_read = PARQUET_BATCH * ROW_BYTES * COPIES
    decode = source_bytes + points * (ROW_BYTES * COPIES + 3 * 8 * 5)
    return dict(
        normalized_row_allowance_bytes=ROW_BYTES,
        peak_copy_allowance=COPIES,
        baseline_allowance_bytes=BASELINE_BYTES,
        point_batch=point_batch,
        parquet_batch=PARQUET_BATCH,
        max_expvers=2,
        normalized_full_day_estimate_bytes=normalized_day,
        bounded_working_set_estimate_bytes=BASELINE_BYTES + max(decode, aggregate + parquet_read),
        policy="preallocation_model_and_cooperative_RSS_stop_not_OS_hard_allocation_guarantee",
    )


class LimitExceeded(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    memory_bytes: int = 512 * MIB
    staging_bytes: int = 1024 * MIB
    transfer_bytes: int = 128 * MIB
    requests: int = 4
    seconds: float = 3600
    workers: int = 1


class Budget:
    def __init__(self, limits: Limits, *, rss=None, peak_rss=None, clock=None, staging_root=None):
        if (
            any(
                type(v) is not int or v <= 0
                for v in (
                    limits.memory_bytes,
                    limits.staging_bytes,
                    limits.transfer_bytes,
                    limits.requests,
                )
            )
            or not math.isfinite(limits.seconds)
            or not 0 < limits.seconds <= 3600
            or limits.workers != 1
            or limits.memory_bytes > 1024 * MIB
            or limits.staging_bytes > 2048 * MIB
        ):
            raise ValueError("Positive resource caps and exactly one worker are required.")
        self.limits = limits
        self.staging_root = None if staging_root is None else Path(staging_root).resolve()
        self.clock = clock or time.monotonic
        self.started = self.clock()
        if rss is None:
            process = psutil.Process()
            self.rss = lambda: process.memory_info().rss
        else:
            self.rss = rss
        # Historical high-water reporting is independent from admission.
        self.historical_peak = peak_rss or (
            lambda: int(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                * (1 if sys.platform == "darwin" else 1024)
            )
        )
        self.current_rss = 0
        self.requests = 0
        self.reserved = 0
        self.received = 0
        self.peak_rss = 0
        self.journal = None
        self.previous_elapsed = 0

    def bind_journal(self, path):
        path = Path(path)
        same_run = self.journal == path
        local_elapsed = self.previous_elapsed + self.clock() - self.started
        if self.journal is not None and not same_run:
            raise ValueError("One budget cannot switch persistent transfer runs.")
        if path.exists():
            state = json.loads(path.read_text())
            if state["limits"] != vars(self.limits):
                raise ValueError("Resume requires original resource caps.")
            self.requests = state["requests"]
            self.reserved = state["reserved_transfer_bytes"]
            self.received = state["received_transfer_bytes"]
            self.peak_rss = state["peak_rss_bytes"]
            self.previous_elapsed = (
                max(state["elapsed_seconds"], local_elapsed)
                if same_run
                else state["elapsed_seconds"]
            )
            self.started = self.clock()
        self.journal = path
        self.persist()

    def persist(self):
        if self.journal is not None:
            atomic_json(self.journal, dict(limits=vars(self.limits), **self.receipt()))

    def checkpoint(self, *, additional_memory=0):
        if self.previous_elapsed + self.clock() - self.started > self.limits.seconds:
            raise LimitExceeded("Compute time cap exceeded at cooperative checkpoint.")
        measured = self.rss()
        self.current_rss = measured
        self.peak_rss = max(self.peak_rss, measured, self.historical_peak())
        if measured + additional_memory > self.limits.memory_bytes:
            raise LimitExceeded(
                "Memory cap exceeded by measured RSS plus planned allocation; use a fresh worker/smaller chunk."
            )

    def check_disk(self, root: Path, *, additional_bytes=0):
        self.checkpoint()
        if self.staging_root is not None:
            if not Path(root).resolve().is_relative_to(self.staging_root):
                raise ValueError("Staging path escapes owned aggregate budget root.")
            root = self.staging_root
        size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) if root.exists() else 0
        if size + additional_bytes > self.limits.staging_bytes:
            raise LimitExceeded("Owned staging byte cap exceeded.")

    def write_json(self, path, value):
        atomic_json(Path(path), value)

    def retain_directory(self, path):
        pass  # Legacy aggregate pilot accounting deliberately keeps all siblings.

    def reserve_transfer(self, size):
        self.checkpoint()
        if (
            size <= 0
            or self.requests + 1 > self.limits.requests
            or self.reserved + size > self.limits.transfer_bytes
        ):
            raise LimitExceeded("Request/transfer reservation cap exceeded before network open.")
        self.requests += 1
        self.reserved += size
        self.persist()  # Reserve before opening, including interrupted attempts.

    def receipt(self):
        return dict(
            requests=self.requests,
            reserved_transfer_bytes=self.reserved,
            received_transfer_bytes=self.received,
            peak_rss_bytes=self.peak_rss,
            current_rss_bytes=self.current_rss,
            elapsed_seconds=self.previous_elapsed + self.clock() - self.started,
            enforcement="cooperative estimate/checkpoint checks; streamed byte cap; no OS hard-memory preemption",
        )


def atomic_json(path: Path, value, *, on_replace=None, on_durable=None):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    if on_replace is not None:
        on_replace()
    parent = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
        if on_durable is not None:
            on_durable()
    finally:
        os.close(parent)


def _bounded_transfer_locked(
    open_response, destination: Path, *, budget: Budget, reservation_bytes: int
):
    """Reserve before invoking a caller's approved official result-response opener.

    This does not submit CDS jobs or validate result URL ownership. The caller must
    supply a qualified official acquisition response and hold the one-worker lease.
    Failed/interrupted partials persist for inspection and are never promoted.
    """
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    receipt = destination.with_suffix(destination.suffix + ".receipt.json")
    if partial.exists() or receipt.exists():
        raise FileExistsError("Transfer run already exists; use a new immutable destination.")
    budget.reserve_transfer(reservation_bytes)
    incremental = getattr(budget, "incremental_storage", False)
    if incremental:
        budget.begin_write(partial, reservation_bytes)
    budget.check_disk(destination.parent, additional_bytes=4096)
    state = dict(status="RESERVED", reserved_bytes=reservation_bytes, received_bytes=0)
    budget.write_json(receipt, state)
    try:
        with open_response() as response, partial.open("xb") as f:
            length = response.headers.get("Content-Length")
            if length is not None and (int(length) < 0 or int(length) > reservation_bytes):
                raise LimitExceeded("Response payload exceeds reserved transfer bytes.")
            while True:
                remaining = reservation_bytes - state["received_bytes"]
                if remaining == 0:
                    if length is None:
                        raise LimitExceeded(
                            "Unknown-length response exhausted reservation; stopped before reading extra bytes."
                        )
                    break
                budget.checkpoint(additional_memory=min(65536, remaining))
                if not incremental:
                    budget.check_disk(
                        destination.parent, additional_bytes=min(65536, remaining) + 4096
                    )
                chunk = response.read(min(65536, remaining))
                if not chunk:
                    break
                budget.received += len(chunk)
                state["received_bytes"] += len(chunk)
                if state["received_bytes"] > reservation_bytes:
                    raise LimitExceeded(
                        "Opener returned more bytes than requested; partial not promoted."
                    )
                f.write(chunk)
                if incremental:
                    budget.progress(partial, state["received_bytes"])
            if length is not None and state["received_bytes"] != int(length):
                raise ValueError(
                    "Response ended before declared Content-Length; partial not promoted."
                )
            f.flush()
            os.fsync(f.fileno())
        digest = hashlib.sha256()
        with partial.open("rb") as f:
            while True:
                budget.checkpoint(additional_memory=65536)
                chunk = f.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
        state.update(status="COMPLETE", sha256=digest.hexdigest())
        if incremental:
            budget.finish_write(partial)
        os.replace(partial, destination)
        if incremental:
            budget.move_storage(partial, destination, retained=True)
        budget.write_json(receipt, state)
    except BaseException:
        if (
            incremental
            and str(partial.relative_to(budget.namespace))
            in json.loads(budget.ledger.read_text())["allocations"]
        ):
            budget.finish_write(partial)
        state["status"] = "INCOMPLETE_NOT_PROMOTED"
        budget.write_json(receipt, state)
        raise
    return receipt


def bounded_transfer(open_response, destination: Path, *, budget: Budget, reservation_bytes: int):
    """One persistent owned transfer run; lease and reservation precede network open."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (destination.parent / ".transfer.lock").open("a+b") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another worker owns the bounded transfer run.") from None
        budget.bind_journal(destination.parent / "TRANSFER_BUDGET.json")
        try:
            return _bounded_transfer_locked(
                open_response, destination, budget=budget, reservation_bytes=reservation_bytes
            )
        finally:
            budget.persist()
