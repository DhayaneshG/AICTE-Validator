"""Rule-based document classification (keyword scoring, title-weighted).

Short lines (title/letterhead-like) weigh more than body text. Returns the type, a confidence
(score share) and the evidence line. Low confidence -> 'unknown' (manual review).
"""
from __future__ import annotations

import re

PATTERNS: dict[str, list[tuple[str, float]]] = {
    "affidavit": [(r"\baffidavit\b", 3.0), (r"\bsolemnly affirm\b", 1.5), (r"\bdeponent\b", 1.0), (r"notary public", 1.0)],
    "noc": [(r"no[\s-]*objection", 3.0), (r"\bnoc\b", 2.0), (r"has no objection", 1.5)],
    "lease": [(r"lease deed|deed of lease|land lease agreement|registered lease", 3.0), (r"\blessor\b|\blessee\b", 1.5), (r"demised (land|premises)", 1.0)],
    "affiliation_letter": [(r"affiliation", 3.0), (r"affiliating university", 1.5), (r"conditions of affiliation", 1.0)],
    "accreditation_certificate": [(r"accreditation|accredited", 3.0), (r"programme accreditation", 1.5)],
    "previous_eoa_letter": [(r"extension of approval", 3.0), (r"\beoa letter\b", 2.0), (r"approval is hereby extended", 2.0), (r"approval letter", 1.5)],
}
TITLE_MAX_CHARS = 64


def classify(pages: list[dict]) -> dict:
    scores = {k: 0.0 for k in PATTERNS}
    best_line: dict[str, tuple[float, int, list[float], str]] = {}
    for p in pages[:2]:
        for ln in p["lines"]:
            text = ln["text"].strip()
            low = text.lower()
            short = len(text) <= TITLE_MAX_CHARS
            for dtp, pats in PATTERNS.items():
                for pat, w in pats:
                    if re.search(pat, low):
                        s = w * (2.0 if short else 0.6)
                        scores[dtp] += s
                        if dtp not in best_line or s > best_line[dtp][0]:
                            best_line[dtp] = (s, p["page"], ln["bbox"], text)
    total = sum(scores.values())
    if total <= 0:
        return {"doc_type": "unknown", "confidence": 0.0, "scores": scores, "evidence": None}
    best = max(scores, key=scores.get)
    ordered = sorted(scores.values(), reverse=True)
    margin = (ordered[0] - ordered[1]) / ordered[0] if ordered[0] else 0
    share = scores[best] / total
    conf = round(min(1.0, 0.5 * share + 0.5 * margin + (0.0 if ordered[0] >= 4 else -0.25)), 3)
    ev = best_line.get(best)
    return {"doc_type": best if conf >= 0.4 else "unknown", "confidence": conf, "scores": scores,
            "evidence": {"page": ev[1], "bbox": ev[2], "text": ev[3]} if ev else None}
