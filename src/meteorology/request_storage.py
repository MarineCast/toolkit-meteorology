"""Request-only storage ledger. No eviction; legacy pilot budgets are unchanged.

The request namespace lease excludes concurrent acquisition. A separate storage
lease makes reservations atomic even for independent accounting clients. One
startup reconciliation counts all actual files; per-write deltas need no tree scan.
Interrupted allocations remain reserved until explicitly finished/reconciled.
"""

from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import shutil

from .era5.resources import Budget, LimitExceeded, atomic_json

CONTROL_BYTES = 65536
DISK_MARGIN = 16 * 1024**2


class RequestBudget(Budget):
    incremental_storage = True

    def __init__(self, limits, *, namespace, run_root, retained_cache_bytes=None, **kwargs):
        super().__init__(limits, **kwargs)
        self.namespace = Path(namespace).resolve()
        self.run_root = Path(run_root).resolve()
        self.owner = str(self.run_root.relative_to(self.namespace))
        self.quota = retained_cache_bytes
        self.reconciliations = 0
        self.ledger = self.namespace / "STORAGE_LEDGER.json"
        self.run_root.mkdir(parents=True, exist_ok=True)
        with self._state() as state:
            # Scan once, including failed stages and shared source downloads.
            files = {
                str(p.relative_to(self.namespace)): p.stat().st_size
                for p in self.namespace.rglob("*")
                if p.is_file() and p not in {self.ledger, self.ledger.with_suffix(".json.tmp")}
            }
            self.reconciliations += 1
            state["bytes"] = sum(files.values())
            tracked = state.setdefault("files", {})
            for key, entry in tracked.items():
                entry["bytes"] = files.get(key, 0)
            for key, size in files.items():
                if key.startswith(self.owner + "/") and key not in tracked:
                    tracked[key] = dict(bytes=size, owner=self.owner, retained=False)
            state["owners"] = {}
            state["reserved"] = 0
            for key, entry in tracked.items():
                if not entry["retained"]:
                    owner = state["owners"].setdefault(entry["owner"], dict(bytes=0, reserved=0))
                    owner["bytes"] += entry["bytes"]
            for key, allocation in state["allocations"].items():
                remaining = max(0, allocation["bytes"] - tracked.get(key, {}).get("bytes", 0))
                owner = state["owners"].setdefault(allocation["owner"], dict(bytes=0, reserved=0))
                owner["reserved"] += remaining
                state["reserved"] += remaining
            # Completed retained paths do not belong in the hot ledger. Their
            # aggregate size was reconciled above; only active/failed writes persist.
            state["files"] = {k: v for k, v in tracked.items() if not v["retained"]}
            existing_quota = state.get("retained_cache_quota_bytes")
            if (
                existing_quota is not None
                and self.quota is not None
                and existing_quota != self.quota
            ):
                raise ValueError(
                    "Request cache retains its original namespace quota; use the same quota."
                )
            self.quota = existing_quota if self.quota is None else self.quota
            state["retained_cache_quota_bytes"] = self.quota
            self._admit(state)

    @contextmanager
    def _state(self):
        with (self.namespace / ".storage.lock").open("a+b") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            state = (
                json.loads(self.ledger.read_text())
                if self.ledger.exists()
                else dict(bytes=0, files={}, allocations={})
            )
            yield state
            atomic_json(self.ledger, state)

    def _key(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.namespace):
            raise ValueError("Write escapes owned request cache namespace.")
        return str(path.relative_to(self.namespace))

    def _owner(self, state, owner=None):
        return state["owners"].setdefault(owner or self.owner, dict(bytes=0, reserved=0))

    def _totals(self, state):
        owner = state.get("owners", {}).get(self.owner, dict(bytes=0, reserved=0))
        return CONTROL_BYTES + owner["bytes"] + owner["reserved"], state.get("reserved", 0)

    def _change_size(self, state, key, size):
        entry = state["files"][key]
        previous = entry["bytes"]
        allocation = state["allocations"][key]
        if size > allocation["bytes"]:
            raise LimitExceeded("Written bytes exceed planned storage reservation.")
        delta = size - previous
        state["bytes"] += delta
        self._owner(state, entry["owner"])["bytes"] += delta
        entry["bytes"] = size
        released = max(0, allocation["bytes"] - previous) - max(0, allocation["bytes"] - size)
        self._owner(state, allocation["owner"])["reserved"] -= released
        state["reserved"] -= released

    def _admit(self, state, additional=0):
        self.checkpoint()
        staged, outstanding = self._totals(state)
        if staged + additional > self.limits.staging_bytes:
            raise LimitExceeded("Per-run staging byte cap exceeded.")
        if (
            self.quota is not None
            and state["bytes"]
            + outstanding
            + additional
            + CONTROL_BYTES
            + 2 * len(json.dumps(state).encode())
            > self.quota
        ):
            raise LimitExceeded("Retained-cache quota exceeded; evidence was not evicted.")
        if shutil.disk_usage(self.namespace).free < outstanding + additional + DISK_MARGIN:
            raise LimitExceeded(
                "Insufficient physical disk space for reserved write and safety margin."
            )

    def check_disk(self, root, *, additional_bytes=0):
        self._key(root)
        with self._state() as state:
            self._admit(state, additional_bytes)

    def begin_write(self, path, reservation):
        key = self._key(path)
        with self._state() as state:
            if key in state["allocations"]:
                raise ValueError("Write already has an outstanding storage reservation.")
            size = Path(path).stat().st_size if Path(path).exists() else 0
            previous = state["files"].get(key)
            if previous is not None:
                self._owner(state, previous["owner"])["bytes"] -= previous["bytes"]
            state["files"][key] = dict(bytes=size, owner=self.owner, retained=False)
            self._owner(state)["bytes"] += size
            state["allocations"][key] = dict(bytes=size + reservation, owner=self.owner)
            self._owner(state)["reserved"] += reservation
            state["reserved"] += reservation
            self._admit(state)

    def reserve_more(self, path, size):
        key = self._key(path)
        with self._state() as state:
            state["allocations"][key]["bytes"] += size
            self._owner(state)["reserved"] += size
            state["reserved"] += size
            self._admit(state)

    def progress(self, path, size):
        """Absolute bytes, supplied by the writer; no stat/traversal per chunk."""
        key = self._key(path)
        with self._state() as state:
            self._change_size(state, key, size)

    def finish_write(self, path):
        key = self._key(path)
        size = Path(path).stat().st_size if Path(path).exists() else 0
        with self._state() as state:
            self._change_size(state, key, size)
            allocation = state["allocations"].pop(key)
            remaining = max(0, allocation["bytes"] - size)
            self._owner(state, allocation["owner"])["reserved"] -= remaining
            state["reserved"] -= remaining
            self._admit(state)

    def move_storage(self, source, destination, *, retained=True):
        old, new = self._key(source), self._key(destination)
        with self._state() as state:
            for key in list(state["files"]):
                if key == old or key.startswith(old + "/"):
                    entry = state["files"].pop(key)
                    if retained:
                        self._owner(state, entry["owner"])["bytes"] -= entry["bytes"]
                    else:
                        state["files"][new + key[len(old) :]] = entry
            self._admit(state)

    def retain_directory(self, path):
        prefix = self._key(path)
        with self._state() as state:
            for key, entry in list(state["files"].items()):
                if (key == prefix or key.startswith(prefix + "/")) and entry["owner"] == self.owner:
                    self._owner(state, entry["owner"])["bytes"] -= entry["bytes"]
                    del state["files"][key]
            state["owners"] = {
                k: v for k, v in state["owners"].items() if v["bytes"] or v["reserved"]
            }

    @contextmanager
    def write_file(self, path, reservation):
        self.begin_write(path, reservation)
        try:
            yield
        finally:
            self.finish_write(path)

    def write_json(self, path, value):
        # Atomic JSON temporarily holds both old and new contents. Its temporary
        # shadow is reserved too; final reconciliation charges only committed bytes.
        encoded = len(json.dumps(value, indent=2).encode()) + 1
        with self.write_file(path, encoded):
            atomic_json(Path(path), value)

    def persist(self):
        if self.journal is not None:
            self.write_json(self.journal, dict(limits=vars(self.limits), **self.receipt()))

    def receipt(self):
        result = super().receipt()
        if hasattr(self, "ledger"):
            state = (
                json.loads(self.ledger.read_text())
                if self.ledger.exists()
                else dict(bytes=0, files={}, allocations={})
            )
            staged, outstanding = self._totals(state)
            result["storage"] = dict(
                run_staging_bytes=staged,
                namespace_bytes=state["bytes"],
                outstanding_reservation_bytes=outstanding,
                retained_cache_quota_bytes=self.quota,
                retention_policy="unbounded_no_eviction"
                if self.quota is None
                else "explicit_quota_no_eviction",
                startup_reconciliations=self.reconciliations,
                physical_disk_margin_bytes=DISK_MARGIN,
            )
        return result
