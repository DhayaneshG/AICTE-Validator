"""M7 evaluation of signature validation and image-forensic tamper indicators.

    python -m eval.authenticity_eval --split dev

Tamper: each sample is flagged if the detector set raises at least one indicator; a tampered sample counts as LOCALISED
only if an indicator's box overlaps the true altered region. Clean controls are scans re-saved exactly like the tampered ones.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict

from backend.pipeline import signatures, tamper
from backend.pipeline.orchestrator import load_thresholds

from .common import SYN


def _ov(a, b) -> bool:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1])) > 0


def run_signatures(split: str | None = None) -> dict:
    items = json.loads((SYN / "signed" / "labels.json").read_text())["items"]
    rows = []
    for it in items:
        if split and it["split"] != split:
            continue
        r = signatures.validate_pdf_signatures((SYN / "signed" / it["file"]).read_bytes())
        rows.append({"file": it["file"], "kind": it["kind"], "expected": it["expected_status"], "got": r["status"], "ok": r["status"] == it["expected_status"]})
    return {"n": len(rows), "correct": sum(r["ok"] for r in rows), "rows": rows}


def run_tamper(split: str, params: dict | None = None) -> dict:
    params = params if params is not None else load_thresholds().get("tamper")
    items = [i for i in json.loads((SYN / "tamper" / "labels.json").read_text())["items"] if split in (None, "all", i["split"])]
    per_class = defaultdict(lambda: {"n": 0, "flagged": 0, "localised": 0, "flagged_medium": 0})
    by_detector = defaultdict(Counter)
    tp = fp = fn = tn = 0
    for k, it in enumerate(items):
        regs = tamper.analyze_pdf((SYN / "tamper" / it["file"]).read_bytes(), params)
        c = per_class[it["kind"]]
        c["n"] += 1
        flagged = bool(regs)
        c["flagged"] += flagged
        c["flagged_medium"] += any(r["severity"] == "medium" for r in regs)
        if it["tampered"]:
            loc = any(_ov(r["bbox"], t) or (r.get("source_bbox") and _ov(r["source_bbox"], t)) for r in regs for t in it["regions_pt"])
            c["localised"] += loc
            tp += flagged
            fn += not flagged
        else:
            fp += flagged
            tn += not flagged
        for t in {r["type"] for r in regs}:
            by_detector[t][it["kind"]] += 1
        print(f"  [{k + 1}/{len(items)}] {it['file']} indicators={len(regs)}", file=sys.stderr, flush=True)
    return {"split": split, "n": len(items), "per_class": dict(per_class), "detector_hits_by_class": {k: dict(v) for k, v in by_detector.items()},
            "tampered_flag_recall": tp / (tp + fn) if tp + fn else None, "precision": tp / (tp + fp) if tp + fp else None,
            "clean_false_positive_rate": fp / (fp + tn) if fp + tn else None, "params": params}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    s = run_signatures(a.split)
    print(f"[{a.split}] signatures: {s['correct']}/{s['n']} correct")
    for r in s["rows"]:
        print("   ", r["file"], "expected", r["expected"], "got", r["got"])
    t = run_tamper(a.split)
    print(f"[{a.split}] tamper n={t['n']} recall(flagged)={t['tampered_flag_recall']:.3f} precision={t['precision']:.3f} clean_FPR={t['clean_false_positive_rate']:.3f}")
    for k, v in t["per_class"].items():
        print(f"    {k:10s} n={v['n']} flagged={v['flagged']} localised={v['localised']} medium+={v['flagged_medium']}")
    print("    detector hits by class:", t["detector_hits_by_class"])
    if a.json:
        print(json.dumps({"signatures": s, "tamper": t}))
