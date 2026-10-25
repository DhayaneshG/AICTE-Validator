"""Issuer verification of QR/reference IDs against the MOCK issuer API, with retry + exponential backoff.

Statuses: verified | mismatch | expired | pending | source_unavailable | manual_review.
`mismatch` carries a reason: not_found (reference absent from registry), institution_mismatch,
doc_type_mismatch. Every call stores source, timestamp, the raw response and the attempt history.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Callable, Optional

from backend.mock.issuer_api import SOURCE, MockIssuerAPI, SourceUnavailable

from .types import VerificationResult


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def verify_reference(api: MockIssuerAPI, ref_id: Optional[str], expected_institution_id: Optional[str], doc_type: str,
                     as_of: dt.date, max_attempts: int = 3, backoff_base: float = 0.2,
                     sleep: Callable[[float], None] = time.sleep, now_fn: Callable[[], str] = _now) -> VerificationResult:
    if not ref_id:
        return VerificationResult(status="manual_review", reason="no_reference_found", ref_id=None, doc_type=doc_type,
                                  source=SOURCE, timestamp=now_fn(), raw_response=None, attempts=[])
    attempts: list[dict] = []
    resp = None
    for n in range(1, max_attempts + 1):
        try:
            resp = api.lookup(ref_id)
            attempts.append({"attempt": n, "outcome": "response", "at": now_fn()})
            break
        except SourceUnavailable as e:
            wait = backoff_base * (2 ** (n - 1))
            attempts.append({"attempt": n, "outcome": "source_unavailable", "error": str(e), "at": now_fn(),
                             "backoff_s": wait if n < max_attempts else 0.0})
            if n < max_attempts:
                sleep(wait)
    if resp is None:
        return VerificationResult(status="source_unavailable", reason="source_unavailable_after_retries", ref_id=ref_id,
                                  doc_type=doc_type, source=SOURCE, timestamp=now_fn(), raw_response=None, attempts=attempts)

    def result(status, reason=""):
        return VerificationResult(status=status, reason=reason, ref_id=ref_id, doc_type=doc_type, source=resp.get("source", SOURCE),
                                  timestamp=now_fn(), raw_response=resp, attempts=attempts)

    if not resp.get("found"):
        return result("mismatch", "not_found")
    rec = resp["record"]
    if rec.get("status") == "pending":
        return result("pending", "issuer_confirmation_pending")
    if expected_institution_id and rec.get("institution_id") != expected_institution_id:
        return result("mismatch", "institution_mismatch")
    if rec.get("doc_type") != doc_type:
        return result("mismatch", "doc_type_mismatch")
    if rec.get("valid_until") and dt.date.fromisoformat(rec["valid_until"]) < as_of:
        return result("expired", "registry_record_expired")
    return result("verified", "ok")
