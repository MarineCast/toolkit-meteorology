"""Verify exact approved coastal registry membership and mask witnesses."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import h3
from .artifacts import checksum_path
from .study import load_study_config, resolve_config_path

@dataclass(frozen=True)
class ReportingMembership:
    """Exact loaded IDs and captured evidence; geometry qualification is external."""

    cells: tuple[str, ...]
    resolution: int
    study_identity: dict
    artifact_sha256: str
    canonical_membership_sha256: str
    mask_sha256: str
    mask_revision: str


def _parse_membership_ids(raw: bytes) -> tuple[str, ...]:
    """Consume the coordinated owner interface, not permissive CSV/JSON parsing."""
    if raw == b"":
        return ()
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise ValueError("Membership bytes require LF and terminal newline.")
    try:
        lines = raw.decode("ascii").split("\n")[:-1]
    except UnicodeDecodeError:
        raise ValueError("Membership bytes require ASCII canonical IDs.") from None
    if any(not line or line.strip() != line for line in lines):
        raise ValueError("Membership bytes must have no blank lines, headers or whitespace.")
    return tuple(lines)


def load_reporting_membership(
    study_config,
    *,
    mask_path: str | Path | None = None,
    mask_hash_policy: str | None = None,
    resolution: int = 5,
) -> ReportingMembership:
    """Consume exact owner-declared R5 support after production gates.

    Membership path is config-relative and constrained beneath resolved Data.
    mask_path is an additional explicitly supplied witness supported only when
    the owner mask manifest declares exact-file SHA256. Other geometry hash
    policies require their matching verifier; this function never guesses one.
    """
    selected = resolve_config_path(study_config)
    identity = load_study_config(selected)
    if identity is None:
        raise ValueError("Marine reporting requires explicit shared study selection.")
    if resolution != identity["study_config"]["products"]["meteorology"]["h3_resolution"]:
        raise ValueError("Meteorology marine reporting uses the configured R5 registry only.")
    registry = identity["study_config"]["grid_registry"]
    root = Path(identity["resolved_data_root"])
    if "mask_relative_path" in registry:
        relative_mask = Path(registry["mask_relative_path"])
        declared_mask = (selected.parent / relative_mask).resolve()
        if relative_mask.is_absolute() or not declared_mask.is_relative_to(root):
            raise ValueError("Declared mask must remain beneath shared Data root.")
        if mask_path is not None and Path(mask_path).resolve() != declared_mask:
            raise ValueError("Explicit mask differs from the shared declaration.")
        mask_path = declared_mask
        if mask_hash_policy is not None and mask_hash_policy != registry["mask_hash_policy"]:
            raise ValueError("Explicit mask hash policy differs from the shared declaration.")
        mask_hash_policy = registry["mask_hash_policy"]
        relative_manifest = Path(registry["manifest_relative_path"])
        manifest_path = (selected.parent / relative_manifest).resolve()
        if relative_manifest.is_absolute() or not manifest_path.is_relative_to(root):
            raise ValueError("Mask manifest must remain beneath shared Data root.")
        manifest_raw = manifest_path.read_bytes()
        if hashlib.sha256(manifest_raw).hexdigest() != registry["manifest_sha256"]:
            raise ValueError("Mask manifest SHA256 mismatch.")
        from .study import parse_json_bytes

        manifest = parse_json_bytes(manifest_raw)
        for field in ("mask_revision", "mask_sha256", "mask_hash_policy", "memberships"):
            if manifest[field] != registry[field]:
                raise ValueError("Mask manifest differs from shared registry.")
    entries = [
        m
        for m in registry["memberships"]
        if m["resolution"] == resolution and m["role"] == "water_reporting"
    ]
    if len(entries) != 1:
        raise ValueError("Registry must declare exactly one water_reporting membership at R5.")
    entry = entries[0]
    root = Path(identity["resolved_data_root"])
    relative = Path(entry["relative_path"])
    if relative.is_absolute():
        raise ValueError("Membership artifact path must be relative to selected config.")
    path = (selected.parent / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(
            "Config-relative membership artifact must remain beneath shared Data root."
        )
    raw = path.read_bytes()  # Parse and byte hash are bound to one immutable snapshot.
    artifact_hash = hashlib.sha256(raw).hexdigest()
    if artifact_hash != entry["sha256"]:
        raise ValueError("Membership artifact SHA256 mismatch.")
    if mask_hash_policy != "sha256_exact_file_bytes":
        raise ValueError(
            "Mask hash policy requires its matching owner verifier; no guessed file-byte identity."
        )
    if mask_path is None or not Path(mask_path).is_file():
        raise ValueError("Exact-file mask witness must be a regular file.")
    mask_hash = checksum_path(Path(mask_path))
    if mask_hash != registry["mask_sha256"]:
        raise ValueError("Materialized mask SHA256 mismatch.")
    cells = _parse_membership_ids(raw)
    if len(cells) != entry["count"]:
        raise ValueError("Membership count differs from declared water-reporting registry.")
    if any(
        not isinstance(c, str)
        or c != c.lower()
        or not h3.is_valid_cell(c)
        or h3.get_resolution(c) != resolution
        for c in cells
    ):
        raise ValueError("Membership requires valid lowercase H3 IDs at the declared resolution.")
    if cells != tuple(sorted(set(cells))):
        raise ValueError("Membership artifact IDs must already be sorted and unique.")
    canonical_hash = hashlib.sha256(
        ("\n".join(cells) + "\n").encode() if cells else b""
    ).hexdigest()
    return ReportingMembership(
        cells,
        resolution,
        identity,
        artifact_hash,
        canonical_hash,
        mask_hash,
        registry["mask_revision"],
    )
