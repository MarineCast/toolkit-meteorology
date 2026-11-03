"""Recoverable publication primitives shared by domain-layer producers."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import uuid
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from .contracts import atomic_write_json


def _fsync_directory(path: Path) -> None:
    """Flush directory-entry changes where the platform supports it."""

    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class TransactionalFamilyPublisher(AbstractContextManager["TransactionalFamilyPublisher"]):
    """Stage and recoverably promote a related set of filesystem artifacts.

    Promotion uses durable journals and per-destination backups because replacing
    multiple paths cannot be one operating-system transaction. A manifest may be
    declared terminal and is then promoted last.
    """

    JOURNAL_SCHEMA_VERSION = 2

    def __init__(self, parent: str | Path, run_id: str | None = None):
        self.parent = Path(parent).resolve()
        self.run_id = run_id or f"publication-{uuid.uuid4().hex[:12]}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.run_id):
            raise ValueError("Publication run_id must be one safe path component.")
        # The external display ID never determines a path or replaces prior staging.
        token = uuid.uuid4().hex
        self.staging = self.parent / ".staging" / token
        self.transactions = self.parent / ".transactions"
        self.transaction = self.transactions / token
        self.journal = self.transaction / "journal.json"
        self.staging_owner = self.staging.with_suffix(".owner.json")
        self.committed = False
        self._items: dict[Path, dict[str, Any]] = {}
        self._ownership = None

    @staticmethod
    @contextmanager
    def _exclusive_parent(parent: Path):
        """Own the entire recovery namespace; reject overlapping writers.

        Never unlink the lock file: another process may hold the same inode.
        Kernel ownership is released automatically after process termination.
        """
        parent.mkdir(parents=True, exist_ok=True)
        with (parent / ".publication.lock").open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"Publication parent is busy: {parent}") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> "TransactionalFamilyPublisher":
        self._ownership = self._exclusive_parent(self.parent)
        self._ownership.__enter__()
        try:
            for private_root in (self.staging.parent, self.transactions):
                if private_root.is_symlink():
                    raise ValueError(f"Publication control directory is a symlink: {private_root}")
            self._recover_owned(self.parent)
            self._recover_staging(self.parent)
            self._write_journal(self.staging_owner, self._staging_identity(self.parent, self.staging.name))
            self.staging.mkdir(parents=True, exist_ok=False)
            _fsync_directory(self.staging.parent)
            return self
        except BaseException:
            self._ownership.__exit__(None, None, None)
            self._ownership = None
            raise

    @staticmethod
    def _staging_identity(parent, token):
        return dict(owner='meteorology-publication-staging-v1', control=str(parent), token=token)

    @classmethod
    def _clean_staging(cls, parent, marker):
        """Only remove a marked run under the locked control namespace."""
        token = marker.name.removesuffix('.owner.json')
        staging = parent / '.staging' / token
        if (not re.fullmatch(r'[0-9a-f]{32}', token) or marker.is_symlink()
                or staging.is_symlink() or json.loads(marker.read_text()) != cls._staging_identity(parent, token)):
            raise RuntimeError(f'Staging ownership mismatch; preserved: {marker}')
        if staging.exists():
            shutil.rmtree(staging)
        marker.unlink()
        _fsync_directory(marker.parent)

    @classmethod
    def _recover_staging(cls, parent):
        # Recovery has already resolved all transactions. Unmarked legacy or
        # caller-created directories are deliberately not adopted or swept.
        for marker in sorted((parent / '.staging').glob('*.owner.json')):
            try:
                cls._clean_staging(parent, marker)
            except OSError as error:
                raise RuntimeError(f'Staging cleanup incomplete; retry recovery: {marker}') from error

    @staticmethod
    def _clean_transaction(directory):
        # Keep the durable journal until all backups have been removed. If
        # deletion fails, the next run still knows whether this was committed.
        backups = directory / 'backups'
        if backups.exists():
            shutil.rmtree(backups)
        (directory / 'journal.tmp').unlink(missing_ok=True)
        (directory / 'journal.json').unlink(missing_ok=True)
        directory.rmdir()

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    @staticmethod
    def _write_journal(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)

    @classmethod
    def _rollback_payload(cls, payload: Mapping[str, Any]) -> None:
        touched_parents: set[Path] = set()
        for item in reversed(list(payload.get("items", []))):
            destination = Path(str(item["destination"]))
            backup = Path(str(item["backup"]))
            touched_parents.add(destination.parent)
            if backup.exists():
                cls._remove_path(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, destination)
            elif not item.get("had_destination") and (
                item.get("promoted")
                or (destination.exists() and not Path(str(item.get("candidate", ""))).exists())
            ):
                cls._remove_path(destination)
        for parent in touched_parents:
            if parent.exists():
                _fsync_directory(parent)

    @classmethod
    def _recover_owned(cls, parent: Path) -> None:
        transactions = parent / ".transactions"
        if not transactions.exists():
            return
        for directory in sorted(path for path in transactions.iterdir() if path.is_dir()):
            if directory.is_symlink():
                raise RuntimeError(f"Symlinked transaction; preserved: {directory}")
            journal = directory / "journal.json"
            if not journal.exists():
                # Before the initial journal, no destination rename is allowed.
                # Nonempty orphan backups still require manual reconstruction.
                if any(path.is_file() for path in directory.rglob("*")):
                    raise RuntimeError(f"Recovery evidence has no journal; preserved: {directory}")
                shutil.rmtree(directory)
                continue
            try:
                payload = json.loads(journal.read_text(encoding="utf-8"))
                if (
                    not isinstance(payload, dict)
                    or payload.get("phase") not in {"promoting", "committed"}
                    or not isinstance(payload.get("items"), list)
                ):
                    raise ValueError("Invalid publication journal structure")
                for item in payload["items"]:
                    if not all(
                        key in item
                        for key in ("destination", "backup", "candidate", "had_destination")
                    ):
                        raise ValueError("Incomplete publication journal item")
                if payload["phase"] != "committed":
                    cls._rollback_payload(payload)
            except Exception as exc:
                raise RuntimeError(
                    f"Recovery failed; journal and remaining backups preserved: {directory}"
                ) from exc
            cls._clean_transaction(directory)
            _fsync_directory(transactions)
        try:
            transactions.rmdir()
        except OSError:
            pass
        _fsync_directory(parent)

    def stage_path(self, destination: str | Path, *, terminal: bool = False) -> Path:
        final = Path(destination).resolve()
        if final in self._items:
            raise ValueError(f"Destination already staged: {final}")
        candidate = self.staging / f"{len(self._items):03d}_{final.name}"
        self._items[final] = {"candidate": candidate, "terminal": bool(terminal)}
        return candidate

    def stage_manifest_path(self, destination: str | Path) -> Path:
        """Stage the transaction's terminal manifest."""

        if any(item["terminal"] for item in self._items.values()):
            raise ValueError("Only one terminal manifest may be staged per transaction.")
        return self.stage_path(destination, terminal=True)

    def candidate_path(self, destination: str | Path) -> Path:
        """Return the staged candidate registered for a destination."""

        final = Path(destination).resolve()
        try:
            return Path(self._items[final]["candidate"])
        except KeyError as exc:
            raise KeyError(f"Destination has not been staged: {final}") from exc

    def stage_manifest(self, destination: str | Path, payload: Mapping[str, Any]) -> Path:
        staged = self.stage_manifest_path(destination)
        atomic_write_json(staged, dict(payload), overwrite=True)
        return staged

    def publish(self) -> tuple[Path, ...]:
        if self._ownership is None:
            raise RuntimeError("Publication requires exclusive context ownership")
        missing = [
            item["candidate"] for item in self._items.values() if not item["candidate"].exists()
        ]
        if missing:
            raise FileNotFoundError(f"Staged artifact is missing: {missing[0]}")
        terminals = [destination for destination, item in self._items.items() if item["terminal"]]
        if len(terminals) > 1:
            raise ValueError("A publication transaction may have at most one terminal manifest.")
        ordered = sorted(self._items.items(), key=lambda pair: pair[1]["terminal"])
        if self.transaction.exists():
            raise RuntimeError(f"Unrecovered transaction evidence: {self.transaction}")
        backup_root = self.transaction / "backups"
        backup_root.mkdir(parents=True)
        _fsync_directory(self.transactions)
        payload: dict[str, Any] = {
            "schema_version": self.JOURNAL_SCHEMA_VERSION,
            "run_id": self.run_id,
            "phase": "promoting",
            "items": [],
        }
        for index, (destination, item) in enumerate(ordered):
            payload["items"].append(
                {
                    "destination": str(destination),
                    "candidate": str(item["candidate"]),
                    "backup": str(backup_root / f"{index:03d}_{destination.name}"),
                    "had_destination": destination.exists(),
                    "backed_up": False,
                    "promoted": False,
                    "terminal": item["terminal"],
                }
            )
        try:
            self._write_journal(self.journal, payload)
            for item in payload["items"]:
                destination = Path(item["destination"])
                candidate = Path(item["candidate"])
                backup = Path(item["backup"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    os.replace(destination, backup)
                    _fsync_directory(destination.parent)
                    _fsync_directory(backup.parent)
                    item["backed_up"] = True
                    self._write_journal(self.journal, payload)
                os.replace(candidate, destination)
                _fsync_directory(destination.parent)
                item["promoted"] = True
                self._write_journal(self.journal, payload)
            payload["phase"] = "committed"
            self._write_journal(self.journal, payload)
            self.committed = True
        except Exception as error:
            # A failed durable commit checkpoint must be recoverable as an abort.
            payload["phase"] = "promoting"
            if self.journal.exists():
                self._write_journal(self.journal, payload)
            self._rollback_payload(payload)
            try:
                self._clean_transaction(self.transaction)
            except OSError as cleanup_error:
                error.add_note(f'Rollback completed, but transaction cleanup is incomplete: {cleanup_error}')
            raise
        try:
            self._clean_transaction(self.transaction)
        except OSError as error:
            raise RuntimeError('Publication committed; transaction cleanup incomplete. Retry to recover.') from error
        try:
            self.transactions.rmdir()
        except OSError:
            pass
        _fsync_directory(self.parent)
        return tuple(destination for destination, _ in ordered)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            # Candidates help disambiguate interrupted renames. Keep them when
            # failed rollback left a transaction requiring later recovery.
            if not self.transaction.exists() and self.staging_owner.exists():
                try:
                    self._clean_staging(self.parent, self.staging_owner)
                except (OSError, RuntimeError) as error:
                    message = ('Publication committed' if self.committed else 'Publication not committed')
                    message += '; staging cleanup incomplete. Retry to recover.'
                    if exc is not None:
                        exc.add_note(message)
                    else:
                        raise RuntimeError(message) from error
        finally:
            if self._ownership is not None:
                self._ownership.__exit__(exc_type, exc, traceback)
                self._ownership = None


__all__ = ["TransactionalFamilyPublisher"]
