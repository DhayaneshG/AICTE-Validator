"""M2 extraction + classification evaluation on digital PDFs.   python -m eval.extract_eval --split dev"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from backend.pipeline.classify import classify
from backend.pipeline.extract import decode_qr_pdf, extract_pdf
from backend.pipeline.fields import _parse, extract_fields

from .common import apps, doc_path, load_labels, norm_ws, truth_value

DOC_TYPES = json.loads((__import__("pathlib").Path(__file__).resolve().parents[1] / "backend/config/doc_types.json").read_text())


def run(split: str, verbose: bool = False) -> dict:
    labels = load_labels()
    per_field = defaultdict(lambda: [0, 0])
    cls_ok = cls_n = 0
    bbox_ok = bbox_n = 0
    qr_ok = qr_n = 0
    errors = []
    for a in apps(labels, split):
        for d in a["documents"]:
            data = doc_path(a, d).read_bytes()
            ex = extract_pdf(data)
            c = classify(ex["pages"])
            cls_n += 1
            cls_ok += c["doc_type"] == d["doc_type"]
            if c["doc_type"] != d["doc_type"]:
                errors.append((a["application_id"], d["filename"], "classify", d["doc_type"], c["doc_type"]))
            f = extract_fields(ex["pages"])
            for field in DOC_TYPES[d["doc_type"]]["expected_fields"]:
                truth = truth_value(d, field)
                if truth is None:
                    continue
                got = f.get(field)
                gv = got.value if got else None
                ok = (norm_ws(gv) == norm_ws(truth)) if isinstance(truth, str) else gv == truth
                per_field[field][0] += ok
                per_field[field][1] += 1
                if not ok:
                    errors.append((a["application_id"], d["filename"], field, truth, gv))
                if got and got.bbox and ok:
                    # does the highlighted bbox actually contain the value? re-parse the text inside it
                    page = ex["pages"][got.page - 1]
                    txt = " ".join(cc["text"] for ln in page["lines"] for cc in ln["cells"]
                                   if cc["bbox"][0] >= got.bbox[0] - 1 and cc["bbox"][2] <= got.bbox[2] + 1
                                   and cc["bbox"][1] >= got.bbox[1] - 1 and cc["bbox"][3] <= got.bbox[3] + 1)
                    reparsed = _parse(field, txt)[0]
                    bbox_n += 1
                    bbox_ok += (norm_ws(reparsed) == norm_ws(truth)) if isinstance(truth, str) else reparsed == truth
            if truth_value(d, "reference_id"):
                qr_n += 1
                q = decode_qr_pdf(data)
                qr_ok += bool(q) and q[0]["ref_id"] == truth_value(d, "reference_id")
    tot_ok = sum(v[0] for v in per_field.values())
    tot_n = sum(v[1] for v in per_field.values())
    res = {"split": split, "docs": cls_n, "classification_accuracy": cls_ok / cls_n,
           "field_accuracy_overall": tot_ok / tot_n, "field_accuracy_n": tot_n,
           "field_accuracy_by_field": {k: {"correct": v[0], "n": v[1], "acc": v[0] / v[1]} for k, v in sorted(per_field.items())},
           "bbox_contains_value": (bbox_ok / bbox_n if bbox_n else None), "qr_decode_accuracy": qr_ok / qr_n if qr_n else None,
           "qr_n": qr_n, "errors": errors}
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    a = ap.parse_args()
    r = run(a.split)
    print(f"[{r['split']}] docs={r['docs']} classification={r['classification_accuracy']:.3f}  "
          f"field_acc={r['field_accuracy_overall']:.3f} (n={r['field_accuracy_n']})  bbox_contains_value={r['bbox_contains_value']:.3f}  qr={r['qr_decode_accuracy']:.3f} (n={r['qr_n']})")
    for k, v in r["field_accuracy_by_field"].items():
        print(f"   {k:18s} {v['correct']}/{v['n']} = {v['acc']:.3f}")
    for e in r["errors"][:25]:
        print("   ERR", e)
