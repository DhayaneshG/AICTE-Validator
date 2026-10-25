"""M2 tests: extraction, classification, fields, QR decoding on digital PDFs."""
import io
from pathlib import Path

import pymupdf
import pytest

from backend.pipeline.classify import classify
from backend.pipeline.extract import decode_qr_pdf, extract_pdf, parse_pdf_date
from backend.pipeline.fields import extract_fields, parse_date
from eval.common import apps, doc_path, load_labels, norm_ws, truth_value
from eval.extract_eval import run as extract_eval


@pytest.fixture(scope="module")
def labels():
    return load_labels()


def test_text_layer_and_bboxes(labels):
    a = labels["applications"][0]
    d = a["documents"][0]
    ex = extract_pdf(doc_path(a, d).read_bytes())
    assert ex["has_text_layer"] and ex["page_count"] == d["pages"]
    for p in ex["pages"]:
        for ln in p["lines"]:
            for c in ln["cells"]:
                x0, y0, x1, y1 = c["bbox"]
                assert 0 <= x0 < x1 <= p["width"] + 1 and 0 <= y0 < y1 <= p["height"] + 1
    assert ex["metadata"]["creation_date"] == d["creation_date"]


def test_image_only_pdf_has_no_text_layer():
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200), False)
    pix.clear_with(200)
    page.insert_image(page.rect, pixmap=pix)
    ex = extract_pdf(doc.tobytes())
    assert ex["has_text_layer"] is False and ex["pages"][0]["has_text"] is False


def test_date_parsing_formats():
    iso = "2026-03-12"
    for s in ["12/03/2026", "12 March 2026", "March 12, 2026", "12-Mar-2026", "12.03.2026", "2026-03-12"]:
        assert parse_date(s) == iso, s
    assert parse_date("31/02/2026") is None and parse_date("no date") is None
    assert parse_pdf_date("D:20260312101500+00'00'") == iso


def test_classification_dev_perfect_and_unknown_for_garbage(labels):
    n = ok = 0
    for a in apps(labels, "dev"):
        for d in a["documents"]:
            c = classify(extract_pdf(doc_path(a, d).read_bytes())["pages"])
            n += 1
            ok += c["doc_type"] == d["doc_type"]
    assert ok / n >= 0.95
    assert classify([{"page": 1, "lines": [{"line": 0, "text": "lorem ipsum dolor", "bbox": [0, 0, 1, 1], "cells": []}]}])["doc_type"] == "unknown"


def test_qr_decoded_matches_label(labels):
    n = 0
    for a in apps(labels, "dev"):
        for d in a["documents"]:
            ref = truth_value(d, "reference_id")
            if ref:
                q = decode_qr_pdf(doc_path(a, d).read_bytes())
                assert q and q[0]["ref_id"] == ref and q[0]["page"] >= 1 and len(q[0]["bbox"]) == 4
                n += 1
    assert n >= 10


def test_dev_field_accuracy_meets_target():
    r = extract_eval("dev")
    assert r["field_accuracy_overall"] >= 0.90, r["errors"][:5]
    assert r["bbox_contains_value"] >= 0.90


def test_sidebar_rows_not_chained():
    """Regression: sidebar label/value rows must not merge with the main-column baseline (APP-0002)."""
    labels = load_labels()
    a = next(x for x in labels["applications"] if x["application_id"] == "APP-0002")
    d = next(x for x in a["documents"] if x["filename"] == "upload_1.pdf")
    f = extract_fields(extract_pdf(doc_path(a, d).read_bytes())["pages"])
    assert f["intake"].value == d["fields"]["intake"]
    assert f["expiry_date"].value == d["fields"]["expiry_date"]


def test_low_confidence_when_value_malformed():
    page = {"page": 1, "width": 595, "height": 842, "text": "Date of Issue: 3x/zz/2026", "lines": [
        {"line": 0, "text": "Date of Issue : 3x/zz/2026", "bbox": [50, 50, 300, 62], "cells": [
            {"text": "Date of Issue :", "bbox": [50, 50, 120, 62], "line": 0, "cell": 0, "conf": 1.0},
            {"text": "3x/zz/2026", "bbox": [140, 50, 200, 62], "line": 0, "cell": 1, "conf": 1.0}]}]}
    f = extract_fields([page])
    assert f["issue_date"].value is None and f["issue_date"].confidence < 0.3
