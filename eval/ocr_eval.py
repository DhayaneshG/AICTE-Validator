"""M6 OCR evaluation on synthetic scans (printed English only).

    python -m eval.ocr_eval --split dev [--level 1] [--limit N]

Per degradation level: character error rate (vs the digital text layer of the source PDF), field accuracy, classification
accuracy, QR decode rate, seconds/page, and how many wrong fields were flagged low-confidence (-> manual review) instead of silently accepted.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict

from rapidfuzz.distance import Levenshtein

from backend.pipeline.extract import extract_pdf
from backend.pipeline.orchestrator import Pipeline, load_thresholds
from backend.pipeline.ocr_router import route

from .common import SYN, load_labels, norm_ws, truth_value

DOC_TYPES = json.loads((SYN.parents[1] / "backend/config/doc_types.json").read_text())


def _flat(pages) -> str:
    return " ".join(" ".join(p["text"].split()) for p in pages)


def run(split: str, levels=(1, 2, 3), limit: int | None = None, thresholds: dict | None = None) -> dict:
    labels = {a["application_id"]: a for a in load_labels()["applications"]}
    man = json.loads((SYN / "scans" / "manifest.json").read_text())
    pipe = Pipeline(thresholds=thresholds or load_thresholds(), ocr_router=route)
    low = pipe.thresholds["low_confidence_below"]
    out = {}
    for lv in levels:
        items = [i for i in man["items"] if i["level"] == lv and labels[i["scan"].split("/")[2]]["split"] == split]
        if limit:
            items = items[:limit]
        cer_d = cer_n = 0
        f_ok = f_n = 0
        wrong_flagged = wrong_silent = right_flagged = 0
        cls_ok = cls_n = qr_ok = qr_n = 0
        secs = pages = 0.0
        per_field = defaultdict(lambda: [0, 0])
        errors = []
        for it in items:
            app = labels[it["scan"].split("/")[2]]
            fname = it["scan"].split("/")[-1]
            doc = next(d for d in app["documents"] if d["filename"] == fname)
            data = (SYN / it["scan"]).read_bytes()
            t0 = time.perf_counter()
            ctx = pipe.analyze_file("x", fname, data)
            secs += time.perf_counter() - t0
            print(f"  [L{lv}] {len(per_field) and ''}{it['scan'].split('/', 2)[2]} done ({secs:.0f}s elapsed)", file=sys.stderr, flush=True)
            pages += ctx.page_count
            gt = extract_pdf((SYN / it["source"]).read_bytes())
            a, b = _flat(gt["pages"]), _flat(ctx.pages)
            cer_d += Levenshtein.distance(a, b)
            cer_n += len(a)
            cls_n += 1
            cls_ok += ctx.doc_type == doc["doc_type"]
            for field in DOC_TYPES[doc["doc_type"]]["expected_fields"]:
                truth = truth_value(doc, field)
                if truth is None:
                    continue
                got = ctx.fields.get(field)
                gv = got.value if got else None
                ok = (norm_ws(gv) == norm_ws(truth)) if isinstance(truth, str) else gv == truth
                flagged = got is None or got.value is None or got.confidence < low
                f_n += 1
                f_ok += ok
                per_field[field][0] += ok
                per_field[field][1] += 1
                if ok and flagged:
                    right_flagged += 1
                if not ok:
                    wrong_flagged += flagged
                    wrong_silent += not flagged
                    errors.append((app["application_id"], fname, field, truth, gv, round(got.confidence, 2) if got else None))
            if truth_value(doc, "reference_id"):
                qr_n += 1
                qr_ok += bool(ctx.qr) and ctx.qr[0]["ref_id"] == truth_value(doc, "reference_id")
        out[lv] = {"files": len(items), "pages": int(pages), "cer": cer_d / cer_n if cer_n else None,
                   "field_accuracy": f_ok / f_n if f_n else None, "fields_n": f_n,
                   "wrong_fields_flagged_for_review": wrong_flagged, "wrong_fields_silent": wrong_silent,
                   "correct_fields_flagged_for_review": right_flagged,
                   "classification_accuracy": cls_ok / cls_n if cls_n else None,
                   "qr_decode_rate": qr_ok / qr_n if qr_n else None, "qr_n": qr_n,
                   "seconds_per_page": secs / pages if pages else None,
                   "field_accuracy_by_field": {k: [v[0], v[1]] for k, v in sorted(per_field.items())}, "errors": errors}
    return {"split": split, "levels": out}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--level", type=int)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = run(a.split, (a.level,) if a.level else (1, 2, 3), a.limit)
    for lv, v in r["levels"].items():
        print(f"[{r['split']} L{lv}] files={v['files']} pages={v['pages']} CER={v['cer']:.4f} field_acc={v['field_accuracy']:.3f} (n={v['fields_n']}) "
              f"cls={v['classification_accuracy']:.3f} qr={v['qr_decode_rate']} ({v['qr_n']}) {v['seconds_per_page']:.2f}s/page  "
              f"wrong: flagged={v['wrong_fields_flagged_for_review']} silent={v['wrong_fields_silent']}")
        for e in v["errors"][:12]:
            print("    ERR", e)
    if a.json:
        print(json.dumps(r))
