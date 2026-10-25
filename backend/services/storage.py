"""Encrypted document storage on local disk (portable to object storage). Files are never stored in plaintext."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from . import crypto

STORAGE = Path(os.environ.get("AICTE_STORAGE", crypto.VAR / "storage"))


def save(application_id: str, data: bytes) -> str:
    """Encrypt and write; returns a path relative to the storage root."""
    d = STORAGE / application_id
    d.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.enc"
    (d / name).write_bytes(crypto.encrypt(data))
    return f"{application_id}/{name}"


def read(rel_path: str) -> bytes:
    return crypto.decrypt((STORAGE / rel_path).read_bytes())


def delete(rel_path: str) -> bool:
    p = STORAGE / rel_path
    if p.exists():
        p.unlink()
        return True
    return False
