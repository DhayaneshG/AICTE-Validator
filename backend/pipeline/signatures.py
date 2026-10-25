"""Digital-signature validation for PDFs (pyHanko) against the MOCK trust root.

Outcome statuses: no_signature | valid | modified_after_signing | invalid_integrity | untrusted_signer | expired_certificate |
signer_not_authorised | error.  Anything other than valid/no_signature is an advisory indicator for human review.
"""
from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path

import logging

for _n in ("pyhanko", "pyhanko_certvalidator"):  # expected validation failures are reported as statuses, not logged as errors
    logging.getLogger(_n).setLevel(logging.CRITICAL)

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "backend" / "config" / "signature_policy.json"
TRUST_NOTE = "MOCK trust root (synthetic) - not a real certificate authority"


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text())


def _trust_roots(policy: dict):
    from asn1crypto import pem, x509
    roots = []
    for rel in policy["trust_roots"]:
        raw = (ROOT / rel).read_bytes()
        _, _, der = pem.unarmor(raw)
        roots.append(x509.Certificate.load(der))
    return roots


def validate_pdf_signatures(data: bytes, now: dt.datetime | None = None, policy: dict | None = None) -> dict:
    """Validate every embedded signature; returns {status, signatures: [...], trust_root, note}. Never raises.

    pyHanko drives its own asyncio loop, which fails inside a running loop (e.g. an async FastAPI handler), so the work
    always runs in a short-lived worker thread."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_validate, data, now, policy).result()


def _validate(data: bytes, now: dt.datetime | None = None, policy: dict | None = None) -> dict:
    policy = policy or load_policy()
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {"status": "no_signature", "signatures": [], "trust_root": "MOCK", "note": TRUST_NOTE}
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        sigs = list(reader.embedded_signatures)
    except Exception as e:  # unreadable structure
        return {**out, "status": "error", "error": f"{type(e).__name__}: {e}"}
    if not sigs:
        return out
    worst = "valid"
    rank = {"valid": 0, "signer_not_authorised": 1, "expired_certificate": 2, "untrusted_signer": 3, "modified_after_signing": 4, "invalid_integrity": 5, "error": 6}
    try:
        roots = _trust_roots(policy)
    except Exception as e:
        return {**out, "status": "error", "error": f"trust root unavailable: {e}"}
    for sig in sigs:
        info = {"field": sig.field_name}
        status = "valid"
        try:
            cert = sig.signer_cert
            info.update({"signer_cn": cert.subject.native.get("common_name"), "signer_org": cert.subject.native.get("organization_name"),
                         "not_before": cert.not_valid_before.isoformat(), "not_after": cert.not_valid_after.isoformat()})
            vc = ValidationContext(trust_roots=roots, allow_fetching=False, moment=now)
            st = validate_pdf_signature(sig, signer_validation_context=vc)
            info.update({"intact": bool(st.intact), "crypto_valid": bool(st.valid), "trusted": bool(st.trusted),
                         "coverage": str(st.coverage), "modification_level": str(st.modification_level)})
            covers_all = "ENTIRE_FILE" in str(st.coverage)
            if not (st.intact and st.valid):
                status = "invalid_integrity"
            elif not covers_all or "NONE" not in str(st.modification_level).upper() and "FORM" not in str(st.modification_level).upper() and "ANNOT" not in str(st.modification_level).upper():
                status = "modified_after_signing"
            elif not st.trusted:
                status = "expired_certificate" if cert.not_valid_after < now or cert.not_valid_before > now else "untrusted_signer"
            elif info.get("signer_org") not in policy["authorised_signer_orgs"]:
                status = "signer_not_authorised"
        except Exception as e:
            status = "error"
            info["error"] = f"{type(e).__name__}: {e}"
        info["status"] = status
        out["signatures"].append(info)
        if rank[status] > rank[worst]:
            worst = status
    out["status"] = worst
    return out
