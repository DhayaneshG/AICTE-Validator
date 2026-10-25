"""Map findings to labelled defect types and score precision/recall (app level and doc level)."""
from __future__ import annotations

from collections import defaultdict


def finding_defect(f) -> str | None:
    """Defect type implied by a HARD finding (status open). manual_review/low-confidence findings do not count."""
    rid = f.rule_id
    d = f.details
    if rid.startswith("R-MISS-"):
        return "missing_document"
    if rid == "R-EXP-LEASE":
        return "expired_lease"
    if rid == "R-EXP-ACCREDITATION_CERTIFICATE":
        return "expired_accreditation"
    if rid in ("R-NAME-CONSIST", "R-REC-NAME"):
        return "name_mismatch"
    if rid == "R-REC-INTAKE":
        return "intake_mismatch"
    if rid == "R-DUP":
        return "duplicate_file"
    if rid == "R-META":
        return "metadata_inconsistent"
    if rid.startswith("R-VER-") and d.get("verification_status") == "mismatch":
        if d.get("reason") == "not_found":
            return "qr_absent"
        if d.get("reason") == "institution_mismatch":
            return "qr_wrong_institution"
    return None


def finding_doc(f) -> str | None:
    return f.details.get("missing_doc_type") or f.details.get("doc_type")


def hard(f) -> bool:
    return f.status == "open" and f.severity in ("medium", "high") or (f.status == "open" and finding_defect(f) is not None)


def score(labels: dict, results: dict, split: str, defect_types: list[str]) -> dict:
    apps = [a for a in labels["applications"] if a["split"] == split]
    app_c = defaultdict(lambda: [0, 0, 0])  # tp, fp, fn
    doc_c = defaultdict(lambda: [0, 0, 0])
    clean_fp = defaultdict(int)
    other_hard = defaultdict(int)  # hard findings that map to no labelled defect (e.g. expired noc)
    for a in apps:
        truth_app = {x["defect_type"] for x in a["defects"]}
        truth_doc = {(x["defect_type"], x["doc_type"]) for x in a["defects"]}
        pred_app, pred_doc = set(), set()
        for f in results[a["application_id"]]["findings"]:
            if f.status != "open":
                continue
            dtp = finding_defect(f)
            if dtp is None:
                if f.severity in ("medium", "high"):
                    other_hard[f.rule_id] += 1
                continue
            pred_app.add(dtp)
            pred_doc.add((dtp, finding_doc(f)))
        for t in defect_types:
            p, g = t in pred_app, t in truth_app
            app_c[t][0] += p and g
            app_c[t][1] += p and not g
            app_c[t][2] += g and not p
            if p and not g and a["label"] == "clean":
                clean_fp[t] += 1
        for t in defect_types:
            pd = {x for x in pred_doc if x[0] == t}
            gd = {x for x in truth_doc if x[0] == t}
            doc_c[t][0] += len(pd & gd)
            doc_c[t][1] += len(pd - gd)
            doc_c[t][2] += len(gd - pd)

    def prf(c):
        tp, fp, fn = c
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        f1 = 2 * p * r / (p + r) if p and r else (0.0 if (p is not None and r is not None) else None)
        return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}

    return {"split": split, "apps": len(apps),
            "per_defect_app": {t: prf(app_c[t]) for t in defect_types},
            "per_defect_doc": {t: prf(doc_c[t]) for t in defect_types},
            "clean_app_false_positives": dict(clean_fp), "other_hard_findings": dict(other_hard)}


def fmt(r: dict) -> str:
    lines = [f"[{r['split']}] apps={r['apps']}", f"{'defect':26s} {'P':>6s} {'R':>6s} {'F1':>6s} {'TP':>3s} {'FP':>3s} {'FN':>3s}   | doc-level P / R"]
    for t, m in r["per_defect_app"].items():
        d = r["per_defect_doc"][t]
        s = lambda x: "  n/a" if x is None else f"{x:6.2f}"
        lines.append(f"{t:26s} {s(m['precision'])} {s(m['recall'])} {s(m['f1'])} {m['tp']:3d} {m['fp']:3d} {m['fn']:3d}   | {s(d['precision'])} / {s(d['recall'])}")
    lines.append(f"clean-app false positives: {r['clean_app_false_positives'] or 'none'}   other hard findings: {r['other_hard_findings'] or 'none'}")
    return "\n".join(lines)
