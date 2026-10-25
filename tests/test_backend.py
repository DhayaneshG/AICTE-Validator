"""M4 product-layer tests: audit chain, versioning, finding carry-over, reviewer-action validation, endpoints."""
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest


from tests.conftest import login  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.app import seed  # noqa: E402
from backend.app.db import SessionLocal, engine, init_db  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app import models as M  # noqa: E402
from backend.services import audit, storage  # noqa: E402

SYN = Path(__file__).resolve().parents[1] / "data" / "synthetic"
LABELS = {a["application_id"]: a for a in json.loads((SYN / "labels.json").read_text())["applications"]}


@pytest.fixture(scope="module")
def client():
    init_db()
    db = SessionLocal()
    seed.seed_institutions(db)
    db.commit()
    seed.seed_applications(db, limit=12)
    seed.seed_users(db)
    db.commit()
    db.close()
    with TestClient(app) as c:
        c.headers.update(login(c))  # default: admin_senior; RBAC tests use their own headers
        yield c


def _app_with_findings(client):
    for i in client.get("/api/applications").json()["items"]:
        if i["counts"]["open_total"] > 0:
            return i["id"]


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_queue_and_detail(client):
    q = client.get("/api/applications").json()
    assert q["count"] == 12
    prios = [i["priority"] for i in q["items"]]
    assert prios == sorted(prios, reverse=True)
    d = client.get("/api/applications/APP-0001").json()
    assert d["institution"]["name"] and d["documents"] and d["checklist"]["required_documents"]
    assert client.get("/api/applications/NOPE").status_code == 404


def test_audit_chain_ok_and_detects_tamper(client):
    assert client.get("/api/audit/verify").json()["ok"] is True
    with pytest.raises(Exception):
        with engine.begin() as cx:
            cx.execute(text("UPDATE audit_log SET actor='evil' WHERE id=1"))
    with pytest.raises(Exception):
        with engine.begin() as cx:
            cx.execute(text("DELETE FROM audit_log WHERE id=1"))
    db = SessionLocal()
    row = db.get(M.AuditLog, 1)
    row.actor = "evil"
    with pytest.raises(Exception):
        db.commit()
    db.rollback()
    db.close()
    assert audit.verify_chain(SessionLocal())["ok"] is True


def test_findings_have_evidence_and_advisory(client):
    aid = _app_with_findings(client)
    fs = client.get(f"/api/applications/{aid}/findings").json()
    assert fs and all(f["message"] and f["advisory"] for f in fs)


def test_reviewer_action_validation_and_audit(client):
    aid = _app_with_findings(client)
    f = next(x for x in client.get("/api/findings", params={"status": "open"}).json() if x["document_id"])
    aid = f["application_id"]
    assert client.post(f"/api/findings/{f['id']}/actions", json={"action": "accept", "reason": "ok"}).status_code == 422
    assert client.post(f"/api/findings/{f['id']}/actions", json={"action": "bogus", "reason": "long enough reason"}).status_code in (400, 422)
    r = client.post(f"/api/findings/{f['id']}/actions", json={"action": "override", "reason": "verified manually with original"})
    assert r.status_code == 201 and r.json()["finding"]["status"] == "overridden"
    log = client.get("/api/audit", params={"entity_type": "finding", "entity_id": f["id"]}).json()
    assert any(a["actor"] == "admin" for a in log["items"])
    assert client.get("/api/audit/verify").json()["ok"] is True


def test_document_view_is_audited_and_file_encrypted(client):
    before = client.get("/api/audit", params={"action": "document.view"}).json()["total"]
    r = client.get("/api/documents/1/page/1.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert client.get("/api/audit", params={"action": "document.view"}).json()["total"] == before + 1
    assert client.get("/api/documents/1/page/99.png").status_code == 404
    db = SessionLocal()
    p = storage.STORAGE / db.get(M.Document, 1).storage_path
    db.close()
    assert not p.read_bytes().startswith(b"%PDF")


def test_versioning_and_carryover(client):
    fs = client.get("/api/findings", params={"status": "overridden"}).json()
    assert fs, "previous test should have overridden one finding"
    aid = fs[0]["application_id"]
    d = client.get(f"/api/applications/{aid}").json()
    decided = [f for f in d["findings"] if f["status"] == "overridden" and f["active"]]
    assert decided, "previous test should have overridden one finding"
    docid = decided[0]["document_id"]
    doc = next(x for x in d["documents"] if x["id"] == docid)
    data = (SYN / "applications" / aid / doc["filename"]).read_bytes()
    # re-upload identical bytes as a new version -> same findings, reviewer decision carried over
    r = client.post(f"/api/applications/{aid}/documents", files={"file": (doc["filename"], data, "application/pdf")},
                    data={"replaces_document_id": str(docid)})
    assert r.status_code == 201, r.text
    assert r.json()["document"]["version"] == doc["version"] + 1
    d2 = client.get(f"/api/applications/{aid}").json()
    old = next(x for x in d2["documents"] if x["id"] == docid)
    assert old["is_current"] is False
    active = [f for f in d2["findings"] if f["active"]]
    assert any(f["status"] == "overridden" and f["carried_from"] for f in active)
    # replacing a superseded version is rejected
    r = client.post(f"/api/applications/{aid}/documents", files={"file": ("x.pdf", data, "application/pdf")}, data={"replaces_document_id": str(docid)})
    assert r.status_code == 409


def test_upload_rejects_non_pdf_and_empty(client):
    assert client.post("/api/applications/APP-0001/documents", files={"file": ("a.pdf", b"", "application/pdf")}).status_code == 400
    assert client.post("/api/applications/APP-0001/documents", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 415


def test_create_application_and_report(client):
    inst = client.get("/api/applications/APP-0001").json()["institution"]["id"]
    r = client.post("/api/applications", json={"institution_id": inst, "category": "eoa_renewal_basic"})
    assert r.status_code == 201
    assert client.post("/api/applications", json={"institution_id": inst, "category": "nope"}).status_code == 422
    rep = client.get("/api/applications/APP-0001/report").json()
    assert rep["scrutiny_history"]["mock"] is True and "advisory" in rep
    meta = client.get("/api/meta").json()
    assert len(meta["mock_components"]) == 4
