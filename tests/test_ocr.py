"""M6: preprocessing, OCR routing, OCR-tolerant IDs, image upload, and the scan -> rectify loop."""
import json
import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest


from tests.conftest import login  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import seed  # noqa: E402
from backend.app.db import SessionLocal, init_db  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.pipeline import preprocess  # noqa: E402
from backend.pipeline.extract import extract_pdf, render_page_array  # noqa: E402
from backend.pipeline.fields import repair_ids  # noqa: E402
from backend.pipeline.orchestrator import Pipeline  # noqa: E402
from backend.pipeline.ocr_router import route  # noqa: E402

SYN = Path(__file__).resolve().parents[1] / "data" / "synthetic"
LABELS = {a["application_id"]: a for a in json.loads((SYN / "labels.json").read_text())["applications"]}
APP = "APP-0002"


def _scan(level, name):
    return (SYN / "scans" / f"L{level}" / APP / name).read_bytes()


def _digital(name):
    return (SYN / "applications" / APP / name).read_bytes()


def test_scans_exist_and_are_image_only():
    man = json.loads((SYN / "scans" / "manifest.json").read_text())
    assert len(man["items"]) >= 150 and {i["level"] for i in man["items"]} == {1, 2, 3}
    ex = extract_pdf(_scan(1, "upload_1.pdf"))
    assert ex["has_text_layer"] is False and ex["pages"][0]["n_images"] == 1


def test_deskew_recovers_rotation():
    g = render_page_array(_digital("upload_1.pdf"), 1, 100)
    h, w = g.shape
    rot = cv2.warpAffine(g, cv2.getRotationMatrix2D((w / 2, h / 2), 2.0, 1.0), (w, h), borderValue=255)
    est = preprocess.estimate_skew(rot)
    fixed, _ = preprocess.deskew(rot)
    assert abs(abs(est) - 2.0) <= 0.75
    assert abs(preprocess.estimate_skew(fixed)) < 0.75


def test_repair_ids():
    assert repair_ids("CR0/1-99247496268/2025/E0A") == "CRO/1-99247496268/2025/EOA"
    assert repair_ids("ERO/1 -30995987559/2025/EOA") == "ERO/1-30995987559/2025/EOA"
    assert repair_ids("NOC-SWR0-2O25-0004l05") == "NOC-SWRO-2025-0004105"
    assert repair_ids("no identifiers here 2025") == "no identifiers here 2025"


@pytest.fixture(scope="module")
def pipe():
    return Pipeline(ocr_router=route)


def test_ocr_routing_extracts_fields_and_boxes(pipe):
    ctx = pipe.analyze_file("1", "upload_1.pdf", _scan(1, "upload_1.pdf"))
    truth = LABELS[APP]["documents"][0]
    assert ctx.source == "ocr" and ctx.doc_type == truth["doc_type"]
    assert "ocr_s" in ctx.timings
    pg = ctx.pages[0]
    assert pg["source"] == "ocr" and pg["lines"]
    for f in ctx.fields.values():
        assert 0.0 <= f.confidence <= 1.0
        if f.bbox:
            assert 0 <= f.bbox[0] < f.bbox[2] <= pg["width"] + 1 and 0 <= f.bbox[1] < f.bbox[3] <= pg["height"] + 1
    assert any(f.value for f in ctx.fields.values())


def test_digital_pdf_does_not_use_ocr(pipe):
    ctx = pipe.analyze_file("1", "upload_1.pdf", _digital("upload_1.pdf"))
    assert ctx.source == "text_layer" and "ocr_s" not in ctx.timings


@pytest.fixture(scope="module")
def client():
    init_db()
    db = SessionLocal()
    seed.seed_institutions(db)
    db.commit()
    seed.seed_users(db)
    db.commit()
    db.close()
    with TestClient(app) as c:
        c.headers.update(login(c))  # default: admin_senior; RBAC tests use their own headers
        yield c


def test_png_upload_is_wrapped_and_ocrd(client):
    inst = LABELS[APP]["institution_id"]
    r = client.post("/api/applications", json={"institution_id": inst, "category": LABELS[APP]["category"]})
    assert r.status_code == 201
    aid = r.json()["id"]
    gray = render_page_array(_scan(1, "upload_1.pdf"), 1, 200)
    ok, png = cv2.imencode(".png", gray)
    up = client.post(f"/api/applications/{aid}/documents", files={"file": ("photo.png", png.tobytes(), "image/png")})
    assert up.status_code == 201, up.text
    doc = up.json()["document"]
    assert doc["filename"] == "photo.pdf" and doc["source"] == "ocr" and doc["page_count"] == 1
    assert client.get(f"/api/documents/{doc['id']}/page/1.png").status_code == 200
    assert client.post(f"/api/applications/{aid}/documents", files={"file": ("x.png", b"\x89PNG\r\n\x1a\nbroken", "image/png")}).status_code in (400, 422)
    tiny = cv2.imencode(".png", np.full((50, 50), 255, np.uint8))[1].tobytes()
    assert client.post(f"/api/applications/{aid}/documents", files={"file": ("t.png", tiny, "image/png")}).status_code == 422


def test_rectification_loop_poor_scan_then_replacement(client):
    inst = LABELS[APP]["institution_id"]
    aid = client.post("/api/applications", json={"institution_id": inst, "category": LABELS[APP]["category"]}).json()["id"]
    name = "upload_3.pdf"
    d1 = client.post(f"/api/applications/{aid}/documents", files={"file": (name, _scan(3, name), "application/pdf")}).json()
    low = [f for f in client.get(f"/api/applications/{aid}/findings").json() if f["document_id"] == d1["document"]["id"] and f["status"] == "manual_review"]
    assert low, "a heavily degraded scan should route fields to manual review"
    d2 = client.post(f"/api/applications/{aid}/documents", files={"file": (name, _digital(name), "application/pdf")},
                     data={"replaces_document_id": str(d1["document"]["id"])}).json()
    assert d2["document"]["version"] == 2
    detail = client.get(f"/api/applications/{aid}").json()
    assert not any(f["status"] == "manual_review" and f["active"] and f["document_id"] == d2["document"]["id"] for f in detail["findings"])
    old = next(d for d in detail["documents"] if d["id"] == d1["document"]["id"])
    assert old["is_current"] is False  # history kept
    assert any(f["document_id"] == d1["document"]["id"] and not f["active"] for f in detail["findings"])
