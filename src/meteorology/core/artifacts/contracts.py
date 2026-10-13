from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


def atomic_write_json(
    path: str | Path,
    payload: Mapping[str, Any] | Sequence[Any],
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically write JSON without silently replacing an existing product."""

    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Artifact already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
        os.replace(temporary, destination)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination


def atomic_write_text(
    path: str | Path, text: str, *, overwrite: bool = False, encoding: str = "utf-8"
) -> Path:
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Artifact already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
        os.replace(temporary, destination)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination
