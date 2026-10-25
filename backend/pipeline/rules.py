"""Checklist-driven validation rules.

A checklist (backend/config/checklists/*.json) is a list of {id, doc, check, severity, message}. The `check` string
selects a handler in CHECKS; the JSON decides which rules run, on which documents, and at what severity, so
checklists can change without code changes. All findings are ADVISORY indicators for human review.

Low-confidence fields never raise a hard flag: they produce a `manual_review` finding (severity low) instead.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from rapidfuzz import fuzz

from . import anomaly
from .types import FieldValue, Finding, VerificationResult

ABBR = {"inst": "institute", "engg": "engineering", "coll": "college", "tech": "technology", "univ": "university"}
STOP = {"autonomous", "the"}


@dataclass
class DocCtx:
    document_id: str
    filename: str
    doc_type: str
    classification: dict
    fields: dict[str, FieldValue]
    pages: list
    metadata: dict
    sha256: str
    qr: list = field(default_factory=list)
    source: str = "text_layer"
    page_count: int = 1
    signature: Optional[dict] = None
    tamper: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    version: int = 1


@dataclass
class AppCtx:
    application_id: str
    category: str
    institution_id: Optional[str]
    submission_date: dt.date
    docs: list[DocCtx]
    checklist: dict
    thresholds: dict
    records: Optional[dict] = None
    seen_hashes: dict = field(default_factory=dict)
    verifications: dict[str, VerificationResult] = field(default_factory=dict)
    _n: int = 0
    now: Optional[dt.datetime] = None
    id_prefix: str = ""

    def new_id(self) -> str:
        self._n += 1
        return f"F-{self.application_id}{self.id_prefix}-{self._n:03d}"

    def docs_for(self, rule: dict) -> list[DocCtx]:
        sel = rule["doc"]
        if sel == "*":
            return list(self.docs)
        sel = [sel] if isinstance(sel, str) else sel
        return [d for d in self.docs if d.doc_type in sel]


# ------------------------------------------------------------------ helpers
def normalize_name(s: str, strip: tuple[str, ...] = ()) -> str:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\bm/s\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [ABBR.get(t, t) for t in s.split()]
    return " ".join(t for t in toks if t not in STOP and t not in strip)


def name_similarity(a: str, b: str, strip: tuple[str, ...] = ()) -> float:
    return float(fuzz.token_sort_ratio(normalize_name(a, strip), normalize_name(b, strip)))


def strip_tokens(ctx: AppCtx) -> tuple[str, ...]:
    if not ctx.records:
        return ()
    toks = []
    for k in ("city", "state"):
        toks += re.sub(r"[^a-z0-9 ]", " ", str(ctx.records.get(k, "")).lower()).split()
    return tuple(toks)


def lowconf(fv: Optional[FieldValue], ctx: AppCtx) -> bool:
    return fv is None or fv.value is None or fv.confidence < ctx.thresholds["low_confidence_below"]


def evidence(fv: Optional[FieldValue]) -> Optional[str]:
    if fv is None:
        return None
    return f"{fv.label}: {fv.raw}" if fv.label else (fv.raw or str(fv.value))


def mk(ctx: AppCtx, rule: dict, doc: Optional[DocCtx], *, source: str = "rule", message: Optional[str] = None,
       fv: Optional[FieldValue] = None, evidence_text: Optional[str] = None, confidence: Optional[float] = None,
       status: str = "open", severity: Optional[str] = None, page=None, bbox=None, details: Optional[dict] = None) -> Finding:
    d = {"check": rule["check"], "doc_type": doc.doc_type if doc else None}
    d.update(details or {})
    return Finding(
        id=ctx.new_id(), application_id=ctx.application_id, document_id=doc.document_id if doc else None,
        rule_id=rule["id"], severity=severity or rule["severity"], source=source,
        message=message or rule["message"], evidence_text=evidence_text if evidence_text is not None else evidence(fv),
        page=page if page is not None else (fv.page if fv else None), bbox=bbox if bbox is not None else (fv.bbox if fv else None),
        confidence=round(confidence if confidence is not None else (fv.confidence if fv else 1.0), 3), status=status,
        created_at=ctx.now or dt.datetime.now(dt.timezone.utc), details=d)


def manual_review(ctx: AppCtx, rule: dict, doc: DocCtx, name: str, fv: Optional[FieldValue]) -> Finding:
    why = "not found" if fv is None or fv.value is None else f"low confidence ({fv.confidence:.2f})"
    return mk(ctx, rule, doc, message=f"Could not reliably read '{name}' on {doc.doc_type.replace('_', ' ')} ({why}); routed to manual review.",
              fv=fv, evidence_text=evidence(fv) or f"{name}: not found", severity="low", status="manual_review",
              confidence=fv.confidence if fv else 0.0, details={"reason": "low_confidence", "field": name})


# ------------------------------------------------------------------ checks
def check_present(ctx: AppCtx, rule: dict) -> list[Finding]:
    want = rule["doc"]
    if any(d.doc_type == want for d in ctx.docs):
        return []
    unknown = [d for d in ctx.docs if d.doc_type == "unknown"]
    got = ", ".join(sorted({d.doc_type for d in ctx.docs})) or "none"
    ev = f"Checklist '{ctx.checklist['id']}' requires '{want}'; documents received (classified): {got}."
    if unknown:
        return [mk(ctx, rule, None, evidence_text=ev + f" {len(unknown)} file(s) could not be classified.", status="manual_review",
                   severity="low", confidence=0.5, details={"missing_doc_type": want, "unclassified_files": [u.filename for u in unknown]})]
    return [mk(ctx, rule, None, evidence_text=ev, details={"missing_doc_type": want})]


def check_not_expired(ctx: AppCtx, rule: dict) -> list[Finding]:
    out = []
    for d in ctx.docs_for(rule):
        fv = d.fields.get("expiry_date")
        if lowconf(fv, ctx):
            out.append(manual_review(ctx, rule, d, "expiry_date", fv))
        elif dt.date.fromisoformat(fv.value) < ctx.submission_date:
            out.append(mk(ctx, rule, d, fv=fv, details={"expiry_date": fv.value, "submission_date": ctx.submission_date.isoformat()},
                          message=f"{rule['message'].rstrip('.')} (expired {fv.value}; submitted {ctx.submission_date.isoformat()})."))
    return out


def check_name_consistent(ctx: AppCtx, rule: dict) -> list[Finding]:
    t = ctx.thresholds["name_similarity_min"]
    strip = strip_tokens(ctx)
    out, ok_docs = [], []
    for d in ctx.docs_for(rule):
        fv = d.fields.get("institution_name")
        if fv is None or fv.value is None:
            continue
        if lowconf(fv, ctx):
            out.append(manual_review(ctx, rule, d, "institution_name", fv))
        else:
            ok_docs.append((d, fv))
    if len(ok_docs) < 2:
        return out
    n = len(ok_docs)
    adj = [[name_similarity(ok_docs[i][1].value, ok_docs[j][1].value, strip) >= t for j in range(n)] for i in range(n)]
    comp, seen = [], set()
    for i in range(n):  # connected components (single link)
        if i in seen:
            continue
        stack, c = [i], []
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            c.append(k)
            stack += [j for j in range(n) if adj[k][j] and j not in seen]
        comp.append(c)
    if len(comp) == 1:
        return out
    comp.sort(key=len, reverse=True)
    majority = comp[0]
    flagged = [i for c in comp[1:] for i in c] if len(comp[0]) > len(comp[1]) else [i for c in comp for i in c]
    ref = ok_docs[majority[0]]
    for i in flagged:
        d, fv = ok_docs[i]
        others = [(od.doc_type, ofv.value) for j, (od, ofv) in enumerate(ok_docs) if j != i]
        best = max(name_similarity(fv.value, ov, strip) for _, ov in others)
        out.append(mk(ctx, rule, d, fv=fv, details={"similarity": best, "threshold": t, "compared_with": others[:3]},
                      message=f"{rule['message'].rstrip('.')}: '{fv.value}' on {d.doc_type.replace('_', ' ')} vs "
                              f"'{ref[1].value}' on {ref[0].doc_type.replace('_', ' ')} (similarity {best:.0f})."))
    return out


def check_id_consistent(ctx: AppCtx, rule: dict) -> list[Finding]:
    out = []
    claimed = ctx.institution_id
    for d in ctx.docs_for(rule):
        fv = d.fields.get("institution_id")
        if fv is not None and fv.value is not None and claimed:
            if lowconf(fv, ctx):
                out.append(manual_review(ctx, rule, d, "institution_id", fv))
            elif fv.value != claimed:
                out.append(mk(ctx, rule, d, fv=fv, details={"found": fv.value, "expected": claimed},
                              message=f"Institution ID {fv.value} on {d.doc_type.replace('_', ' ')} differs from the application's institution ID {claimed}."))
        lf = d.fields.get("letter_id")
        if lf is not None and lf.value is not None:
            if lowconf(lf, ctx):
                out.append(manual_review(ctx, rule, d, "letter_id", lf))
                continue
            parts = lf.value.split("/")
            if claimed and parts[1] != claimed:
                out.append(mk(ctx, rule, d, fv=lf, details={"found": parts[1], "expected": claimed},
                              message=f"Institution ID inside EoA letter number {lf.value} differs from the application's institution ID {claimed}."))
            elif ctx.records and ctx.records.get("last_eoa_letter_id") and lf.value != ctx.records["last_eoa_letter_id"]:
                out.append(mk(ctx, rule, d, fv=lf, details={"found": lf.value, "expected": ctx.records["last_eoa_letter_id"]},
                              message=f"EoA letter number {lf.value} differs from the (MOCK) AICTE record {ctx.records['last_eoa_letter_id']}."))
    return out


def check_matches_records_name(ctx: AppCtx, rule: dict) -> list[Finding]:
    if not ctx.records:
        return [mk(ctx, rule, None, message=f"Institution {ctx.institution_id} not found in the (MOCK) AICTE records.",
                   evidence_text=f"institution_id={ctx.institution_id}", status="manual_review", severity="low", confidence=0.5)]
    t = ctx.thresholds["name_similarity_min"]
    strip = strip_tokens(ctx)
    out = []
    for d in ctx.docs_for(rule):
        fv = d.fields.get("institution_name")
        if fv is None or fv.value is None:
            continue
        if lowconf(fv, ctx):
            out.append(manual_review(ctx, rule, d, "institution_name", fv))
            continue
        sim = name_similarity(fv.value, ctx.records["name"], strip)
        if sim < t:
            out.append(mk(ctx, rule, d, fv=fv, details={"similarity": sim, "threshold": t, "record_name": ctx.records["name"]},
                          message=f"{rule['message'].rstrip('.')}: '{fv.value}' vs record '{ctx.records['name']}' (similarity {sim:.0f})."))
    return out


def check_matches_records_intake(ctx: AppCtx, rule: dict) -> list[Finding]:
    if not ctx.records:
        return []
    out = []
    for d in ctx.docs_for(rule):
        fv = d.fields.get("intake")
        if lowconf(fv, ctx):
            out.append(manual_review(ctx, rule, d, "intake", fv))
        elif fv.value != ctx.records["approved_intake"]:
            out.append(mk(ctx, rule, d, fv=fv, details={"document_intake": fv.value, "records_intake": ctx.records["approved_intake"]},
                          message=f"{rule['message'].rstrip('.')} (document {fv.value}; record {ctx.records['approved_intake']})."))
    return out


def check_issuer_verified(ctx: AppCtx, rule: dict) -> list[Finding]:
    out = []
    for d in ctx.docs_for(rule):
        vr = ctx.verifications.get(d.document_id)
        if vr is None or vr.status == "verified":
            continue
        qr = d.qr[0] if d.qr else None
        fv = d.fields.get("reference_id")
        page = qr["page"] if qr else (fv.page if fv else None)
        bbox = qr["bbox"] if qr else (fv.bbox if fv else None)
        ev = f"QR payload: {qr['payload']}" if qr else (evidence(fv) or "no reference found")
        base = dict(source="verification", evidence_text=ev, page=page, bbox=bbox,
                    details={"verification_status": vr.status, "reason": vr.reason, "ref_id": vr.ref_id})
        if vr.status == "mismatch":
            reg_inst = (vr.raw_response or {}).get("record", {}).get("institution_id")
            msg = {"not_found": f"Reference {vr.ref_id} was not found in the (MOCK) issuer registry.",
                   "institution_mismatch": f"Reference {vr.ref_id} maps to a different institution ({reg_inst}) in the (MOCK) issuer registry.",
                   "doc_type_mismatch": f"Reference {vr.ref_id} is registered for a different document type."}.get(vr.reason, rule["message"])
            out.append(mk(ctx, rule, d, message=msg, confidence=0.9, **base))
        elif vr.status == "expired":
            out.append(mk(ctx, rule, d, message=f"Issuer record for {vr.ref_id} is expired.", confidence=0.9, **base))
        elif vr.status == "pending":
            out.append(mk(ctx, rule, d, message=f"Issuer verification of {vr.ref_id} is pending.", severity="low", confidence=0.9, **base))
        elif vr.status == "source_unavailable":
            out.append(mk(ctx, rule, d, message=f"Issuer source unavailable after {len(vr.attempts)} attempt(s); verification will be retried.",
                          severity="low", status="manual_review", confidence=0.5, **base))
        elif vr.status == "manual_review":
            out.append(mk(ctx, rule, d, message=f"Reference could not be verified automatically ({vr.reason}); routed to manual review.",
                          severity="low", status="manual_review", confidence=0.5, **base))
    return out


def check_metadata_consistent(ctx: AppCtx, rule: dict) -> list[Finding]:
    out = []
    a, b = ctx.thresholds["metadata_days_before_max"], ctx.thresholds["metadata_days_after_max"]
    for d in ctx.docs_for(rule):
        fv = d.fields.get("issue_date")
        cd = d.metadata.get("creation_date")
        if d.source != "text_layer":  # a scan's PDF dates record when it was scanned, not when the document was issued
            continue
        if cd is None or fv is None or fv.value is None or lowconf(fv, ctx):
            continue
        off = anomaly.metadata_offset_days(cd, fv.value)
        if anomaly.metadata_inconsistent(off, a, b):
            direction = "before" if off < 0 else "after"
            out.append(mk(ctx, rule, d, source="anomaly", fv=fv,
                          evidence_text=f"{fv.label}: {fv.raw} | PDF CreationDate: {cd} ({abs(off)} days {direction} stated issue date)",
                          details={"offset_days": off, "pdf_creation_date": cd, "stated_issue_date": fv.value, "before_max": a, "after_max": b}))
    return out


def check_not_duplicate(ctx: AppCtx, rule: dict) -> list[Finding]:
    out = []
    local: dict[str, DocCtx] = {}
    for d in ctx.docs_for(rule):
        hit = anomaly.find_duplicate(d.sha256, ctx.seen_hashes, ctx.application_id)
        if hit:
            out.append(mk(ctx, rule, d, source="anomaly", evidence_text=f"SHA-256 {d.sha256[:16]}... already submitted in {hit[0]} ({hit[1]})",
                          confidence=1.0, details={"duplicate_of_application": hit[0], "duplicate_of_document": hit[1], "sha256": d.sha256}))
        elif d.sha256 in local:
            out.append(mk(ctx, rule, d, source="anomaly", evidence_text=f"SHA-256 {d.sha256[:16]}... identical to {local[d.sha256].filename} in the same application",
                          confidence=1.0, details={"duplicate_of_application": ctx.application_id, "duplicate_of_document": local[d.sha256].document_id, "sha256": d.sha256}))
        local.setdefault(d.sha256, d)
    return out


def check_lease_term_remaining(ctx: AppCtx, rule: dict) -> list[Finding]:
    """APH cl. 2.1(e): EoA is not issued once the live lease is no longer than the longest course; a 30-year extension is needed.
    Fires only for a lease that is NOT yet expired (R-EXP-LEASE covers expired ones) but has less than `min_years` left."""
    out = []
    need = float(rule.get("params", {}).get("min_years", 4))
    for d in ctx.docs_for(rule):
        fv = d.fields.get("expiry_date")
        if lowconf(fv, ctx):
            continue  # R-EXP-* already routes unreadable expiry dates to manual review
        left = (dt.date.fromisoformat(fv.value) - ctx.submission_date).days / 365.25
        if 0 <= left < need:
            out.append(mk(ctx, rule, d, fv=fv, details={"expiry_date": fv.value, "years_left": round(left, 2), "min_years": need, "aph": "2.1(e)"},
                          message=f"{rule['message'].rstrip('.')} ({left:.1f} years left; {need:g} needed)."))
    return out


CHECKS: dict[str, Callable[[AppCtx, dict], list[Finding]]] = {
    "present": check_present,
    "not_expired": check_not_expired,
    "name_consistent": check_name_consistent,
    "id_consistent": check_id_consistent,
    "matches_records_name": check_matches_records_name,
    "matches_records_intake": check_matches_records_intake,
    "issuer_verified": check_issuer_verified,
    "metadata_consistent": check_metadata_consistent,
    "not_duplicate": check_not_duplicate,
    "lease_term_remaining": check_lease_term_remaining,
    # "signature_valid" and "no_tamper_indicator" are registered by backend.pipeline.authenticity (M7/M8)
}


def run_checklist(ctx: AppCtx) -> list[Finding]:
    findings: list[Finding] = []
    for rule in ctx.checklist["rules"]:
        if rule.get("enabled", True) is False:  # opt-in rules ship disabled (set "enabled": true in the checklist JSON)
            continue
        fn = CHECKS.get(rule["check"])
        if fn:
            findings += fn(ctx, rule)
    return findings
