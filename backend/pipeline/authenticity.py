"""Authenticity analyzers (signature validation + image-forensic tamper indicators) and their checklist rules.

Registered into the pipeline by `register(pipeline)`; findings are advisory indicators for human review.
"""
from __future__ import annotations

from . import rules as R
from . import signatures, tamper
from .types import Finding

SIG_MESSAGES = {
    "invalid_integrity": "The digital signature does not verify: the signed content or the signature itself is damaged or altered.",
    "modified_after_signing": "The document was modified after it was digitally signed.",
    "untrusted_signer": "The signer's certificate does not chain to a trusted (MOCK) root.",
    "expired_certificate": "The signer's certificate is expired.",
    "signer_not_authorised": "The signature is cryptographically valid but the signer is not an authorised issuing authority.",
    "error": "The digital signature could not be checked.",
}
SIG_SEVERITY = {"invalid_integrity": "high", "modified_after_signing": "high"}  # others use the checklist's configured severity


def analyze_signature(doc: R.DocCtx, data: bytes) -> None:
    doc.signature = signatures.validate_pdf_signatures(data)


def make_tamper_analyzer(params: dict | None):
    def analyze(doc: R.DocCtx, data: bytes) -> None:
        if doc.source == "text_layer":  # digital page: no image to examine
            doc.tamper = []
            return
        doc.tamper = tamper.analyze_pdf(data, params)
    return analyze


def check_signature_valid(ctx: R.AppCtx, rule: dict) -> list[Finding]:
    out = []
    for d in ctx.docs_for(rule):
        sig = d.signature or {}
        st = sig.get("status", "no_signature")
        if st in ("valid", "no_signature"):
            continue
        s0 = (sig.get("signatures") or [{}])[0]
        out.append(R.mk(ctx, rule, d, source="signature", message=SIG_MESSAGES.get(st, rule["message"]),
                        severity=SIG_SEVERITY.get(st), evidence_text=f"Signature status: {st}; signer: {s0.get('signer_cn')} / {s0.get('signer_org')} (MOCK trust root)",
                        confidence=0.9 if st != "error" else 0.4, details={"signature_status": st, "signatures": sig.get("signatures"), "trust_root": "MOCK"},
                        status="manual_review" if st == "error" else "open"))
    return out


def check_dsc_present(ctx: R.AppCtx, rule: dict) -> list[Finding]:
    """APH cl. 1.4.3 / 1.4.6 / 2.3.4(a): documents must carry the Chairman/Secretary's digital signature (DSC); unsigned uploads are not valid.
    Opt-in (rule ships disabled): the synthetic corpus is mostly unsigned. Invalid signatures are reported by R-SIG, not here."""
    out = []
    for d in ctx.docs_for(rule):
        if (d.signature or {}).get("status", "no_signature") == "no_signature":
            out.append(R.mk(ctx, rule, d, source="signature", message=rule["message"], evidence_text="No digital signature found in the PDF.",
                            details={"aph": "1.4.6", "signature_status": "no_signature"}))
    return out


def check_no_tamper_indicator(ctx: R.AppCtx, rule: dict) -> list[Finding]:
    out = []
    for d in ctx.docs_for(rule):
        for ind in d.tamper or []:
            sev = ind.get("severity", "low")
            sev = sev if sev in ("low", "medium") else "medium"  # tamper indicators never exceed medium
            names = {"ela": "error-level inconsistency", "noise": "unnaturally flat region (possible erasure or overwrite)",
                     "copy_move": "near-identical block duplicated elsewhere on the page", "text_line": "stroke inconsistent with the rest of its line"}
            out.append(R.mk(ctx, rule, d, source="tamper", severity=sev, page=ind["page"], bbox=ind["bbox"],
                            message=f"Image-forensic indicator: {names.get(ind['type'], ind['type'])}. Advisory - may be benign (scan artefact).",
                            evidence_text=f"{ind['type']} score {ind['score']} ({ind.get('detail', '')})", confidence=0.5 if sev == "low" else 0.6,
                            details={"indicator": ind["type"], "score": ind["score"], "source_bbox": ind.get("source_bbox")}))
    return out


def register(pipeline) -> None:
    R.CHECKS["signature_valid"] = check_signature_valid
    R.CHECKS["no_tamper_indicator"] = check_no_tamper_indicator
    R.CHECKS["dsc_present"] = check_dsc_present
    pipeline.extra_analyzers[:] = [a for a in pipeline.extra_analyzers if getattr(a, "__module__", "") != __name__]
    pipeline.extra_analyzers.append(analyze_signature)
    pipeline.extra_analyzers.append(make_tamper_analyzer(pipeline.thresholds.get("tamper")))
