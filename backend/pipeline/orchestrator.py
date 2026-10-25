"""Pipeline orchestrator: per-file analysis (extract -> classify -> fields -> QR) then per-application checks.

`analyze_file` is the expensive step (cacheable); `check_application` runs verification + the data-driven checklist.
Findings are ADVISORY indicators for human review.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Callable, Optional

from backend.mock.issuer_api import MockIssuerAPI
from backend.mock.records_api import MockRecords

from . import rules as R
from .classify import classify
from .extract import decode_qr_pdf, extract_pdf, sha256_bytes
from .fields import extract_fields
from .types import Finding, VerificationResult
from .verify import verify_reference

CONFIG = Path(__file__).resolve().parents[1] / "config"


def load_thresholds(path: Path | None = None) -> dict:
    t = json.loads((path or CONFIG / "thresholds.json").read_text())
    return {k: v for k, v in t.items() if not k.startswith("_") and not k.endswith("provenance")}


def load_checklist(category: str) -> dict:
    return json.loads((CONFIG / "checklists" / f"{category}.json").read_text())


def to_date(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])


class Pipeline:
    def __init__(self, thresholds: dict | None = None, records: MockRecords | None = None, issuer: MockIssuerAPI | None = None,
                 sleep: Callable[[float], None] = lambda s: None, max_attempts: int = 3, backoff_base: float = 0.2,
                 ocr_router=None, extra_analyzers: list | None = None):
        self.thresholds = thresholds or load_thresholds()
        self.records = records or MockRecords()
        self.issuer = issuer or MockIssuerAPI()
        self.sleep, self.max_attempts, self.backoff_base = sleep, max_attempts, backoff_base
        self.ocr_router = ocr_router  # M6: callable(data, extraction) -> extraction with OCR pages
        self.extra_analyzers = extra_analyzers or []  # M7/M8: signature + tamper analyzers, fn(doc, data) -> None

    # ---------------------------------------------------------------- per file
    def analyze_file(self, document_id: str, filename: str, data: bytes, version: int = 1) -> R.DocCtx:
        t: dict[str, float] = {}
        t0 = time.perf_counter()
        ex = extract_pdf(data)
        t["extract_s"] = time.perf_counter() - t0
        source = "text_layer"
        if not ex["has_text_layer"] and self.ocr_router is not None:
            t0 = time.perf_counter()
            ex, source = self.ocr_router(data, ex)
            t["ocr_s"] = time.perf_counter() - t0
        elif not ex["has_text_layer"]:
            source = "no_text"
        t0 = time.perf_counter()
        cls = classify(ex["pages"])
        t["classify_s"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        fields = extract_fields(ex["pages"])
        t["fields_s"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        qr = decode_qr_pdf(data)
        t["qr_s"] = time.perf_counter() - t0
        doc = R.DocCtx(document_id=document_id, filename=filename, doc_type=cls["doc_type"], classification=cls, fields=fields,
                       pages=ex["pages"], metadata=ex["metadata"], sha256=sha256_bytes(data), qr=qr, source=source,
                       page_count=ex["page_count"], timings=t, version=version)
        for fn in self.extra_analyzers:
            fn(doc, data)
        return doc

    # ---------------------------------------------------------------- per application
    def verify_documents(self, ctx: R.AppCtx) -> None:
        verify_types: set[str] = set()
        for rule in ctx.checklist["rules"]:
            if rule["check"] == "issuer_verified":
                d = rule["doc"]
                verify_types |= {d} if isinstance(d, str) else set(d)
        for d in ctx.docs:
            if d.doc_type not in verify_types:
                continue
            qr_ref = d.qr[0]["ref_id"] if d.qr else None
            fv = d.fields.get("reference_id")
            text_ref = fv.value if fv is not None and fv.value and fv.confidence >= ctx.thresholds["low_confidence_below"] else None
            if qr_ref and text_ref and qr_ref != text_ref:
                ctx.verifications[d.document_id] = VerificationResult(
                    status="manual_review", reason="qr_text_mismatch", ref_id=qr_ref, doc_type=d.doc_type,
                    timestamp=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    raw_response={"qr_ref": qr_ref, "printed_ref": text_ref})
                continue
            ctx.verifications[d.document_id] = verify_reference(
                self.issuer, qr_ref or text_ref, ctx.institution_id, d.doc_type, ctx.submission_date,
                max_attempts=self.max_attempts, backoff_base=self.backoff_base, sleep=self.sleep)

    def check_application(self, application: dict, docs: list[R.DocCtx], seen_hashes: dict | None = None,
                          now: dt.datetime | None = None, id_prefix: str = "") -> tuple[list[Finding], R.AppCtx]:
        seen = {} if seen_hashes is None else seen_hashes
        ctx = R.AppCtx(application_id=application["application_id"], category=application["category"],
                       institution_id=application.get("institution_id"), submission_date=to_date(application["submission_date"]),
                       docs=docs, checklist=load_checklist(application["category"]), thresholds=self.thresholds,
                       records=self.records.get(application.get("institution_id")), seen_hashes=seen, now=now, id_prefix=id_prefix)
        self.verify_documents(ctx)
        findings = R.run_checklist(ctx)
        for d in docs:  # register hashes AFTER checking so a file is only a duplicate of EARLIER submissions
            seen.setdefault(d.sha256, (ctx.application_id, d.document_id))
        return findings, ctx

    def process_application(self, application: dict, files: list[tuple[str, str, bytes]], seen_hashes: dict | None = None,
                            now: dt.datetime | None = None) -> dict:
        """files: [(document_id, filename, bytes)]"""
        t0 = time.perf_counter()
        docs = [self.analyze_file(did, fn, data) for did, fn, data in files]
        findings, ctx = self.check_application(application, docs, seen_hashes, now)
        return {"application_id": application["application_id"], "documents": docs, "findings": findings,
                "verifications": ctx.verifications, "ctx": ctx, "seconds": time.perf_counter() - t0}
