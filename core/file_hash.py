"""Stable SHA-256 file hashing for baseline/integrity snapshots."""
from __future__ import annotations

import hashlib
from pathlib import Path

UNAVAILABLE_MISSING = "unavailable:missing-file"


def hash_file(path: Path | str, *, chunk_size: int = 1 << 16) -> str:
    """Return the hex SHA-256 digest of a file's contents.

    Returns `UNAVAILABLE_MISSING` (never a fake/zero hash) when the path does
    not exist or is not a regular file, so callers can distinguish "hashed
    to this value" from "could not be hashed".
    """
    file_path = Path(path)
    if not file_path.is_file():
        return UNAVAILABLE_MISSING
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
