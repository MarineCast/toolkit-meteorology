"""Versioned operational records and explicit publication durability witnesses.

Scientific manifests are immutable. Operational repair runs under the namespace
lease, validates committed evidence, and never resets acquisition counters.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import uuid

from .era5.resources import atomic_json, Limits
from .request import digest
from .request_vintage import CacheError, read_index


class PublicationUncertainError(CacheError):
    publication_status = "UNCERTAIN_RECONCILIATION_REQUIRED"


def read_record(path):
    data = json.loads(Path(path).read_text())
    if data.get("version") != 2 or data.get("sha256") != digest(
        {k: v for k, v in data.items() if k != "sha256"}
    ):
        raise CacheError(f"Invalid operational lifecycle record; reconcile explicitly: {path}")
    if "execution_id" in data:
        expected = digest(
            dict(
                version=2,
                request_id=data.get("request_id"),
                source_generation=data.get("source_generation"),
            )
        )
        if data["execution_id"] != expected or (
            "run_path" in data and data["run_path"] != "runs/" + expected
        ):
            raise CacheError(f"Operational execution binding/path is invalid: {path}")
    return data


def signed(value):
    data = dict(value, version=2)
    data.pop("sha256", None)
    data["sha256"] = digest(data)
    return data


def write_record(path, value, budget=None):
    (budget.write_json if budget else atomic_json)(Path(path), signed(value))


def execution(plan, root, refresh, generation=None):
    profile = root / "refresh-executions" / f"{plan['request_id']}.json"
    if refresh:
        if profile.exists():
            prior = read_record(profile)
            run = root / "runs" / prior["execution_id"]
            state = read_record(run / "RUN_STATE.json") if (run / "RUN_STATE.json").exists() else {}
            if not state.get("status", "").startswith("COMMITTED_"):
                generation = prior["source_generation"]
            else:
                generation = uuid.uuid4().hex
        else:
            generation = uuid.uuid4().hex
    identity = digest(dict(version=2, request_id=plan["request_id"], source_generation=generation))
    run = root / "runs" / identity
    if refresh:
        profile.parent.mkdir(exist_ok=True)
        write_record(
            profile,
            dict(
                execution_id=identity, source_generation=generation, request_id=plan["request_id"]
            ),
        )
    return identity, generation, run


def run_state(budget, binding, status, **extra):
    write_record(budget.run_root / "RUN_STATE.json", dict(binding, status=status, **extra), budget)


def warning(stage, error):
    # Provider messages can contain sensitive details. Only the error class is durable.
    return dict(
        code="OPERATIONAL_RECONCILIATION_REQUIRED", stage=stage, error_type=type(error).__name__
    )


@dataclass
class Publication:
    root: Path
    budget: object
    binding: dict
    record: dict | None = None
    phase: str = "NOT_ACTIVE"

    @property
    def path(self):
        return self.root / "REQUEST_PUBLICATION.json"

    def prepare(self, state, result_id):
        self.record = dict(
            self.binding,
            result_id=result_id,
            index_sha256=state["sha256"],
            run_path=str(self.budget.run_root.relative_to(self.root)),
            limits=vars(self.budget.limits),
            retained_cache_bytes=self.budget.quota,
            status="PREPARED",
            operational_warnings=[],
        )
        write_record(self.path, self.record, self.budget)
        # Reserve the bounded durable commit receipt before touching the pointer.
        self.budget.begin_write(self.path, 8192)
        self.budget.publication = self

    def replaced(self):
        self.phase = "REPLACED_UNCONFIRMED"

    def durable(self):
        # Set the witness immediately after pointer-directory fsync, before any
        # accounting/finalization that can fail. Receipt persistence can itself fail.
        self.phase = "COMMITTED"
        self.record["status"] = "COMMITTED"
        write_record(self.path, self.record)

    def outcome(self):
        if self.phase == "COMMITTED":
            return "COMMITTED"
        if self.phase == "REPLACED_UNCONFIRMED":
            return "UNCERTAIN"
        if self.record is not None:
            try:
                if read_index(self.root).get("sha256") == self.record["index_sha256"]:
                    return "UNCERTAIN"  # Visibility alone cannot certify durability.
            except (OSError, ValueError):
                return "UNCERTAIN"
        return "NOT_ACTIVE"

    def save_outcome(self, status, warnings):
        if self.record is not None:
            self.record.update(status=status, operational_warnings=warnings)
            write_record(self.path, self.record, self.budget if status == "RECONCILED" else None)


def secondary(error, operation):
    """Preserve the primary failure and report a secondary operational write failure."""
    try:
        operation()
    except Exception as extra:
        error.add_note(
            f"Secondary lifecycle operation failed: {type(extra).__name__}; reconciliation required."
        )


def maintain(publication):
    budget = publication.budget
    for path in (publication.root / "ACTIVE_GENERATIONS.json", publication.path):
        state = json.loads(budget.ledger.read_text())
        if budget._key(path) in state["allocations"]:
            budget.finish_write(path)
    budget.retain_directory(publication.root / "generations")
    budget.retain_directory(publication.root / "ACTIVE_GENERATIONS.json")
    run_state(
        budget, publication.binding, "COMMITTED_COMPLETE", result_id=publication.record["result_id"]
    )
    budget.persist()
    budget.retain_directory(budget.run_root)
    budget.retain_directory(
        publication.root / "refresh-executions" / (publication.binding["request_id"] + ".json")
    )
    publication.save_outcome("RECONCILED", [])
    budget.retain_directory(publication.path)


def reconcile_pending(root):
    """Idempotently confirm a visible commit, then repair only its operational writes.

    Caller holds the namespace exclusive lease. No providers or new scientific
    artifacts are involved; the original transfer journal is never reset.
    """
    path = root / "REQUEST_PUBLICATION.json"
    if not path.exists():
        return []
    record = read_record(path)
    if record["status"] == "NOT_ACTIVE":
        # The signed inactive control record is fully written, even when a prior
        # pointer attempt never started. Repair its reservation without touching
        # acquisition counters, partial payloads or active generation selection.
        from .request_storage import RequestBudget

        ledger = root / "STORAGE_LEDGER.json"
        state = json.loads(ledger.read_text()) if ledger.exists() else {}
        key = "REQUEST_PUBLICATION.json"
        if key in state.get("allocations", {}):
            try:
                budget = RequestBudget(
                    Limits(**record["limits"]),
                    namespace=root,
                    run_root=root / record["run_path"],
                    retained_cache_bytes=record["retained_cache_bytes"],
                )
                budget.finish_write(path)
                budget.retain_directory(path)
            except Exception as error:
                return [warning("inactive_control_repair", error)]
        return []
    if record["status"] == "RECONCILED":
        ledger = root / "STORAGE_LEDGER.json"
        state = json.loads(ledger.read_text()) if ledger.exists() else {}
        owner = state.get("owners", {}).get("runs/" + record["execution_id"], {})
        if not owner.get("bytes", 0) and not owner.get("reserved", 0):
            return []
    active = read_index(root)
    if active.get("sha256") != record["index_sha256"]:
        if record["status"] == "COMMITTED":
            raise PublicationUncertainError(
                "Committed pointer changed unexpectedly; explicit reconciliation required."
            )
        write_record(path, dict(record, status="NOT_ACTIVE"))
        return []
    from .request_cache import _manifest, _validate

    result_path, manifest = _manifest(root, record["result_id"])
    _validate(
        result_path.parent, manifest
    )  # Scientific corruption is never an operational warning.
    if (
        manifest["source_generation"] != record["source_generation"]
        or manifest["status"] != "COMPLETE"
    ):
        raise CacheError("Publication receipt does not bind a complete matching source generation.")
    try:
        with (root / "ACTIVE_GENERATIONS.json").open("rb") as stream:
            os.fsync(stream.fileno())
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise PublicationUncertainError(
            "Pointer durability could not be confirmed; retry reconciliation."
        ) from error
    record["status"] = "COMMITTED"
    write_record(path, record)
    from .request_storage import RequestBudget

    try:
        budget = RequestBudget(
            Limits(**record["limits"]),
            namespace=root,
            run_root=root / record["run_path"],
            retained_cache_bytes=record["retained_cache_bytes"],
        )
        # Restore counters for maintenance. Keep elapsed charges but give repair a
        # fresh bounded maintenance clock; acquisition resumption keeps full elapsed.
        journal = budget.run_root / "TRANSFER_BUDGET.json"
        if journal.exists():
            counters = json.loads(journal.read_text())
            budget.requests = counters["requests"]
            budget.reserved = counters["reserved_transfer_bytes"]
            budget.received = counters["received_transfer_bytes"]
            budget.previous_elapsed = 0
        binding = {key: record[key] for key in ("execution_id", "request_id", "source_generation")}
        pub = Publication(root, budget, binding, record, phase="COMMITTED")
        # Never persist a replacement transfer journal during repair. Its original
        # request/reservation/elapsed counters remain unchanged for genuine resumption.
        maintain(pub)
    except Exception as error:
        notices = [warning("committed_accounting_repair", error)]
        secondary(error, lambda: write_record(path, dict(record, operational_warnings=notices)))
        return notices
    return []
