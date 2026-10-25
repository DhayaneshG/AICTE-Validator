"""M8: authenticity + verification wired through the product layer (API), incl. retry of unavailable issuer lookups."""
import json
import os
import tempfile
from pathlib import Path

import pytest


from tests.conftest import login  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import seed  # noqa: E402
from backend.app.db import SessionLocal, init_db  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.services import processing, scheduler, verification  # noqa: E402

SYN = Path(__file__).resolve().parents[1] / "data" / "synthetic"
LABELS = {a["application_id"]: a for a in json.loads((SYN / "labels.json").read_text())["applications"]}
SIGNED = {i["kind"]: i for i in json.loads((SYN / "signed" / "labels.json").read_text())["items"]}
TAMPER = json.loads((SYN / "tamper" / "labels.json").read_text())["items"]


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


def _new_app(client, src="APP-0002"):
    r = client.post("/api/applications", json={"institution_id": LABELS[src]["institution_id"], "category": LABELS[src]["category"]})
    return r.json()["id"]


def test_scheduler_has_retry_job_and_is_off_in_tests():
    assert any(j[0] == "verification_retry" for j in scheduler.JOBS)
    assert scheduler.start() is None  # AICTE_SCHEDULER=0 under pytest


def test_bad_signature_upload_creates_high_finding(client):
    aid = _new_app(client)
    bad = SIGNED["tampered_after_signing"]
    r = client.post(f"/api/applications/{aid}/documents", files={"file": (bad["file"], (SYN / "signed" / bad["file"]).read_bytes(), "application/pdf")})
    assert r.status_code == 201
    doc = r.json()["document"]
    assert doc["signature"]["status"] == "modified_after_signing" and doc["signature"]["trust_root"] == "MOCK"
    f = [x for x in client.get(f"/api/applications/{aid}/findings").json() if x["rule_id"] == "R-SIG"]
    assert len(f) == 1 and f[0]["severity"] == "high" and f[0]["source"] == "signature"


def test_valid_signature_raises_nothing(client):
    aid = _new_app(client)
    ok = SIGNED["valid"]
    r = client.post(f"/api/applications/{aid}/documents", files={"file": (ok["file"], (SYN / "signed" / ok["file"]).read_bytes(), "application/pdf")})
    assert r.json()["document"]["signature"]["status"] == "valid"
    assert not [x for x in client.get(f"/api/applications/{aid}/findings").json() if x["rule_id"] == "R-SIG"]


def test_tampered_scan_upload_creates_tamper_finding_with_bbox(client):
    aid = _new_app(client)
    for it in (i for i in TAMPER if i["kind"] == "erase" and i["split"] == "dev"):
        r = client.post(f"/api/applications/{aid}/documents", files={"file": (it["file"], (SYN / "tamper" / it["file"]).read_bytes(), "application/pdf")})
        assert r.status_code == 201
        fs = [x for x in client.get(f"/api/applications/{aid}/findings").json() if x["rule_id"] == "R-TAMPER" and x["document_id"] == r.json()["document"]["id"]]
        if fs:
            assert all(x["severity"] in ("low", "medium") and x["bbox"] and x["page"] == 1 and x["source"] == "tamper" for x in fs)
            assert r.json()["document"]["tamper"]
            return
    pytest.fail("no dev erase sample produced a tamper finding through the API")


def test_unavailable_issuer_is_retried_and_history_kept(client):
    src = "APP-0001"  # clean application with NOC/affiliation references that the MOCK registry knows
    aid = _new_app(client, src)
    pipe = processing.get_pipeline()
    pipe.issuer.outage_all = True
    try:
        for d in LABELS[src]["documents"]:
            r = client.post(f"/api/applications/{aid}/documents", files={"file": (d["filename"], (SYN / "applications" / src / d["filename"]).read_bytes(), "application/pdf")}, data={"run": "false"})
            assert r.status_code == 201
        assert client.post(f"/api/applications/{aid}/run").status_code == 200
        vs = client.get(f"/api/applications/{aid}/verifications").json()
        assert vs and all(v["status"] == "source_unavailable" for v in vs)
        assert all(len(v["attempts"]) >= 2 and v["mock"] for v in vs)  # retried with backoff, labelled MOCK
        assert any(f["status"] == "manual_review" and f["rule_id"].startswith("R-VER") for f in client.get(f"/api/applications/{aid}/findings").json())
    finally:
        pipe.issuer.outage_all = False
    r = client.post("/api/verifications/retry")
    assert r.status_code == 200 and r.json()["resolved"] >= 1
    vs2 = client.get(f"/api/applications/{aid}/verifications").json()
    assert len(vs2) > len(vs)  # history appended, not overwritten
    latest = {}
    for v in vs2:
        latest[v["document_id"]] = v
    assert all(v["status"] != "source_unavailable" for v in latest.values())
    audit = client.get("/api/audit", params={"action": "verification.retry"}).json()
    assert audit["total"] >= 1
    assert client.get("/api/audit/verify").json()["ok"] is True
    did = vs[0]["document_id"]
    tl = client.get(f"/api/documents/{did}/verifications").json()
    assert [v["status"] for v in tl][0] == "source_unavailable" and tl[-1]["status"] != "source_unavailable"
