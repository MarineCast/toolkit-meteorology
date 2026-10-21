"""Content hashes for source receipts and public weather files."""
import hashlib
from pathlib import Path

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksum_path(path: str | Path) -> str:
    target = Path(path)
    if target.is_file():
        return sha256_file(target)
    if not target.is_dir():
        raise FileNotFoundError(target)
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in target.rglob("*") if candidate.is_file()):
        digest.update(str(item.relative_to(target)).encode("utf-8"))
        digest.update(sha256_file(item).encode("ascii"))
    return digest.hexdigest()
