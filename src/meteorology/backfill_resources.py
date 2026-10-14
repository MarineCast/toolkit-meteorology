"""Shared run admission; short durable leases, independent worker journals."""

from contextlib import contextmanager
from dataclasses import replace
import fcntl
import json
from pathlib import Path
import threading
import time

import psutil

from .era5.resources import atomic_json, LimitExceeded, Limits
from .request import digest
from .request_storage import RequestBudget, CONTROL_BYTES
from .request_lifecycle import secondary


@contextmanager
def lease(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


@contextmanager
def charge_failures(resources):
    """Charge processing time on interruption without replacing its exception."""
    try:
        yield
    except BaseException as error:
        secondary(error, resources.persist)
        raise


class RunResources:
    def __init__(self, directory, *, requests, transfer_bytes, seconds, memory_bytes, workers):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "RESOURCES.json"
        self.lock = threading.RLock()
        self.started = time.monotonic()
        self.metrics = {}
        self.limits = dict(
            requests=requests,
            transfer_bytes=transfer_bytes,
            seconds=seconds,
            memory_bytes=memory_bytes,
            workers=workers,
        )
        if (
            type(requests) is not int
            or requests <= 0
            or type(transfer_bytes) is not int
            or transfer_bytes <= 0
            or not 0 < seconds <= 30 * 86400
        ):
            raise ValueError("Positive backfill resource caps required; max runtime is 30 days.")
        with self.state() as state:
            if state.get("limits", self.limits) != self.limits:
                raise ValueError(
                    "Resume requires original aggregate backfill resource limits/workers."
                )
            state.setdefault("limits", self.limits)
            state.setdefault("requests", 0)
            state.setdefault("reserved_transfer_bytes", 0)
            state.setdefault("received_by_worker", {})
            state.setdefault("elapsed_seconds", 0)
            self.previous_elapsed = state["elapsed_seconds"]
            self.before_requests = state["requests"]
            self.before_received = sum(state["received_by_worker"].values())

    def metric(self, name, value):
        with self.lock:
            self.metrics[name] = self.metrics.get(name, 0) + value

    @contextmanager
    def state(self):
        with self.lock, lease(self.directory / ".resources.lock"):
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            if state and state.get("sha256") != digest(
                {k: v for k, v in state.items() if k != "sha256"}
            ):
                raise ValueError("Backfill resource journal is corrupt; counters cannot be reset.")
            yield state
            state.pop("sha256", None)
            state["version"] = 1
            state["sha256"] = digest(state)
            atomic_json(self.path, state)

    def checkpoint(self, additional_memory=0):
        if self.previous_elapsed + time.monotonic() - self.started > self.limits["seconds"]:
            raise LimitExceeded(
                "Aggregate backfill elapsed-time cap exhausted; original counters preserved."
            )
        if psutil.Process().memory_info().rss + additional_memory > self.limits["memory_bytes"]:
            raise LimitExceeded("Measured backfill RSS plus planned allocation exceeds cap.")

    def reserve(self, size):
        self.checkpoint()
        with self.state() as state:
            if (
                state["requests"] + 1 > self.limits["requests"]
                or state["reserved_transfer_bytes"] + size > self.limits["transfer_bytes"]
            ):
                raise LimitExceeded("Aggregate request/transfer cap exhausted before network open.")
            state["requests"] += 1
            state["reserved_transfer_bytes"] += size
            state["elapsed_seconds"] = self.previous_elapsed + time.monotonic() - self.started

    def persist(self, worker=None):
        with self.state() as state:
            state["elapsed_seconds"] = self.previous_elapsed + time.monotonic() - self.started
            state["peak_rss_bytes"] = max(
                state.get("peak_rss_bytes", 0), psutil.Process().memory_info().rss
            )
            if worker is not None:
                state["received_by_worker"][worker.owner] = worker.received

    def receipt(self):
        self.persist()
        value = json.loads(self.path.read_text())
        value["received_transfer_bytes"] = sum(value["received_by_worker"].values())
        value.pop("sha256", None)
        value["sha256"] = digest(value)
        return value

    def execution(self):
        value = self.receipt()
        return dict(
            provider_requests=value["requests"] - self.before_requests,
            transferred_bytes=value["received_transfer_bytes"] - self.before_received,
            stage_seconds={k: v for k, v in self.metrics.items() if k.endswith("_seconds")},
            operation_counts={k: v for k, v in self.metrics.items() if not k.endswith("_seconds")},
            decodes=self.metrics.get("decodes", 0),
            peak_rss_bytes=value.get("peak_rss_bytes"),
            elapsed_seconds=time.monotonic() - self.started,
        )


class BackfillBudget(RequestBudget):
    """Reuse incremental storage admission; all worker staging is charged together."""

    def __init__(self, resources, namespace, run_root, staging_bytes, quota=None):
        self.resources = resources
        self.historical_staging_bytes = staging_bytes
        self.worker_lock = threading.RLock()
        self.prepared_downloads = set()
        # Existing legacy limits stay unchanged. The new subclass owns extended
        # historical limits, additionally enforced by the aggregate run journal.
        limits = Limits(
            memory_bytes=min(resources.limits["memory_bytes"], 1024**3),
            staging_bytes=min(staging_bytes, 2 * 1024**3),
            transfer_bytes=resources.limits["transfer_bytes"],
            requests=resources.limits["requests"],
            seconds=3600,
        )
        super().__init__(limits, namespace=namespace, run_root=run_root, retained_cache_bytes=quota)
        self.limits = replace(
            self.limits,
            seconds=resources.limits["seconds"],
            memory_bytes=resources.limits["memory_bytes"],
            staging_bytes=staging_bytes,
        )
        self.bind_journal(self.run_root / "TRANSFER_BUDGET.json")

    def _totals(self, state):
        return (
            CONTROL_BYTES * (self.resources.limits["workers"] + 1)
            + sum(owner["bytes"] + owner["reserved"] for owner in state.get("owners", {}).values()),
            state.get("reserved", 0),
        )

    def checkpoint(self, *, additional_memory=0):
        self.limits = replace(
            self.limits,
            seconds=self.resources.limits["seconds"],
            staging_bytes=self.historical_staging_bytes,
            memory_bytes=self.resources.limits["memory_bytes"],
        )
        self.resources.checkpoint(additional_memory)
        # Worker elapsed counters remain charged on same-run retries; only this
        # historical subclass has extended limits. Legacy budgets stay unchanged.
        if self.previous_elapsed + self.clock() - self.started > self.limits.seconds:
            raise LimitExceeded(
                "Worker invocation budget exhausted; resume requires explicit reconciliation."
            )
        measured = self.rss()
        self.current_rss = measured
        self.peak_rss = max(self.peak_rss, measured, self.historical_peak())

    def reserve_transfer(self, size):
        self.resources.reserve(size)
        super().reserve_transfer(size)

    def prepare_download(self, path, size):
        """Admit retained storage before submission, including same-owner resume."""
        key = self._key(path)
        with self._state() as state:
            allocation = state["allocations"].get(key)
            if allocation is not None and allocation != dict(bytes=size, owner=self.owner):
                raise ValueError("Resume requires the original source storage reservation.")
        if allocation is None:
            super().begin_write(path, size)
        self.prepared_downloads.add(key)

    def begin_write(self, path, reservation):
        key = self._key(path)
        if key in self.prepared_downloads:
            self.prepared_downloads.remove(key)
            return
        return super().begin_write(path, reservation)

    def finish_rejected_download(self, path):
        """Release only a proved size-rejected parent's unused reservation."""
        key = self._key(path)
        with self._state() as state:
            outstanding = key in state["allocations"]
        if outstanding:
            self.finish_write(path)
        self.prepared_downloads.discard(key)

    def persist(self):
        super().persist()
        if self.journal is not None:
            self.resources.persist(self)

    def discard_scratch(self, path):
        """Only this invocation's explicitly designated scratch, after publication."""
        path = Path(path)
        if not path.is_relative_to(self.run_root / "scratch"):
            raise ValueError("Only owned backfill scratch may be released.")
        with self._state() as state:
            key = self._key(path)
            if key in state["allocations"]:
                raise ValueError("Outstanding scratch reservation cannot be discarded.")
            entry = state["files"].pop(key, None)
            if entry is not None:
                self._owner(state, entry["owner"])["bytes"] -= entry["bytes"]
            if path.exists():
                state["bytes"] -= path.stat().st_size
                path.unlink()
