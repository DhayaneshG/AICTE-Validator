"""Encryption at rest for stored documents (Fernet / AES-128-CBC + HMAC).

!! STAND-IN FOR A MANAGED KMS !!  The key comes from the AICTE_FERNET_KEY environment variable. If it is not set, a
development key is generated once into var/dev_fernet.key (chmod 600) so the demo works out of the box. In production the
key must come from a managed KMS/HSM with rotation, envelope encryption and access policies - none of which exist here.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("aicte.crypto")
VAR = Path(os.environ.get("AICTE_VAR", Path(__file__).resolve().parents[2] / "var"))


def _load_key() -> bytes:
    env = os.environ.get("AICTE_FERNET_KEY")
    if env:
        return env.encode()
    VAR.mkdir(parents=True, exist_ok=True)
    kf = VAR / "dev_fernet.key"
    if not kf.exists():
        kf.write_bytes(Fernet.generate_key())
        os.chmod(kf, 0o600)
        log.warning("AICTE_FERNET_KEY not set: generated DEV key at %s (stand-in for a managed KMS)", kf)
    return kf.read_bytes().strip()


_fernet: Fernet | None = None


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def reset_key_cache() -> None:
    global _fernet
    _fernet = None


def encrypt(data: bytes) -> bytes:
    return fernet().encrypt(data)


def decrypt(token: bytes) -> bytes:
    try:
        return fernet().decrypt(token)
    except InvalidToken as e:  # wrong key or corrupted/tampered file
        raise ValueError("stored document cannot be decrypted (wrong key or corrupted file)") from e
