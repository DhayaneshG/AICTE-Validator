"""M9: authentication, RBAC, row isolation, audit of views, encryption at rest, retention purge."""
import datetime as dt
import os
import shutil
import tempfile

import pytest


import jwt  # noqa: E402
from tests.conftest import login  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import auth, models as M, seed  # noqa: E402
from backend.app.db import SessionLocal, init_db  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.services import storage  # noqa: E402
from backend.services.clock import clock  # noqa: E402
from eval import rbac_eval  # noqa: E402


@pytest.fixture(scope="module")
def env():
    init_db()
    db = SessionLocal()
    seed.seed_institutions(db)
    db.commit()
    seed.seed_applications(db, limit=8)
    seed.seed_users(db)
    db.commit()
    ctx = rbac_eval.build_ctx(db)
    db.close()
    assert ctx["X"], "fixture needs applications in two regions"
    with TestClient(app) as c:
        yield c, ctx
    clock.reset()


def test_full_rbac_matrix_passes(env):
    c, ctx = env
    s = rbac_eval.summarise(rbac_eval.run_matrix(c, ctx))
    assert s["cases"] >= 50
    assert s["failures"] == [], s["failures"][:3]


def test_public_endpoints_need_no_token(env):
    c, _ = env
    assert c.get("/api/health").status_code == 200
    assert c.get("/health").status_code == 200
    assert c.get("/api/meta").status_code == 200  # login page shows the advisory banner


def test_expired_and_wrong_signature_tokens_rejected(env):
    c, _ = env
    now = dt.datetime.now(dt.timezone.utc)
    expired = jwt.encode({"sub": "admin", "exp": now - dt.timedelta(minutes=1)}, auth._secret(), algorithm="HS256")
    forged = jwt.encode({"sub": "admin", "exp": now + dt.timedelta(hours=1)}, "wrong-secret", algorithm="HS256")
    nonealg = jwt.encode({"sub": "admin"}, None, algorithm="none")
    for tok in (expired, forged, nonealg):
        assert c.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_disabled_user_loses_access_immediately(env):
    c, _ = env
    h = login(c, "scrutiny.cro")
    assert c.get("/api/auth/me", headers=h).status_code == 200
    db = SessionLocal()
    u = db.query(M.User).filter_by(username="scrutiny.cro").one()
    u.active = False
    db.commit()
    assert c.get("/api/auth/me", headers=h).status_code == 401
    assert c.post("/api/auth/login", json={"username": "scrutiny.cro", "password": seed.DEMO_PASSWORD}).status_code == 401
    u.active = True
    db.commit()
    db.close()


def test_passwords_are_bcrypt_hashed(env):
    db = SessionLocal()
    for u in db.query(M.User).all():
        assert u.password_hash.startswith("$2") and seed.DEMO_PASSWORD not in u.password_hash
    db.close()


def test_failed_logins_are_audited_and_lock_out(env):
    c, _ = env
    for _ in range(auth.MAX_FAILS):
        assert c.post("/api/auth/login", json={"username": "inst.b", "password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "inst.b", "password": seed.DEMO_PASSWORD}).status_code == 429  # locked even with the right password
    auth.clear_failures("inst.b")
    assert c.post("/api/auth/login", json={"username": "inst.b", "password": seed.DEMO_PASSWORD}).status_code == 200
    items = c.get("/api/audit?limit=200", headers=login(c)).json()["items"]
    acts = [i["action"] for i in items]
    assert "auth.login_failed" in acts and "auth.login_blocked" in acts and "auth.login" in acts


def test_every_document_view_is_audited_with_the_viewer(env):
    c, ctx = env
    h = login(c, "inst.a")
    before = c.get("/api/audit?action=document.view&limit=500", headers=login(c)).json()["total"]
    assert c.get(f"/api/documents/{ctx['D']}/page/1.png", headers=h).status_code == 200
    assert c.get(f"/api/documents/{ctx['D']}/file", headers=h).status_code == 200
    after = c.get("/api/audit?action=document.view&limit=500", headers=login(c)).json()
    assert after["total"] == before + 2
    assert after["items"][0]["actor"] == "inst.a" and after["items"][0]["role"] == "institution_user"
    # a DENIED view leaves no 'view' row but must not leak existence either
    b = login(c, "inst.b")
    assert c.get(f"/api/documents/{ctx['D']}/file", headers=b).status_code == 404
    assert c.get("/api/audit?action=document.view&limit=500", headers=login(c)).json()["total"] == after["total"]


def test_stored_files_are_encrypted_on_disk(env):
    _, ctx = env
    db = SessionLocal()
    docs = db.query(M.Document).filter(M.Document.storage_path.isnot(None)).all()
    assert docs
    for d in docs:
        raw = (storage.STORAGE / d.storage_path).read_bytes()
        assert not raw.startswith(b"%PDF") and b"%PDF" not in raw[:1024] and b"/Type" not in raw
        assert storage.read(d.storage_path).startswith(b"%PDF")  # decrypts with the key
    db.close()


def test_audit_chain_intact_after_all_of_the_above(env):
    c, _ = env
    r = c.get("/api/audit/verify", headers=login(c)).json()
    assert r["ok"] and r["checked"] > 20


def test_retention_purge(env):
    c, ctx = env
    adm = login(c)
    docs = c.get(f"/api/applications/{ctx['A']}", headers=adm).json()["documents"]
    d0 = docs[0]["id"]
    db = SessionLocal()
    path = db.get(M.Document, d0).storage_path
    db.close()
    assert (storage.STORAGE / path).exists()
    # nothing is old enough today
    assert c.post("/api/admin/purge", headers=adm).json()["purged"] == 0
    try:
        clock.advance(days=400)
        dry = c.post("/api/admin/purge?dry_run=true", headers=adm).json()
        assert dry["dry_run"] and dry["candidates"] > 0 and dry["purged"] == 0 and (storage.STORAGE / path).exists()
        r = c.post("/api/admin/purge", headers=adm).json()
        assert r["purged"] == r["candidates"] > 0
    finally:
        clock.reset()
    assert not (storage.STORAGE / path).exists()  # ciphertext removed from disk
    assert c.get(f"/api/documents/{d0}/file", headers=adm).status_code == 410
    det = c.get(f"/api/documents/{d0}", headers=adm).json()
    assert det["sha256"]  # metadata + hash retained
    items = c.get("/api/audit?action=document.purge&limit=500", headers=adm).json()["items"]
    assert len(items) == r["purged"] and all(i["details"]["sha256"] for i in items)
    assert c.post("/api/admin/purge", headers=adm).json()["purged"] == 0  # idempotent
    assert c.get("/api/audit/verify", headers=adm).json()["ok"]


def test_institutions_list_is_scoped(env):
    c, ctx = env
    inst = c.get("/api/institutions", headers=login(c, "inst.a")).json()
    assert [i["id"] for i in inst] == [ctx["A_inst"]]
    reg = c.get("/api/institutions", headers=login(c, f"scrutiny.{ctx['A_region'].lower()}")).json()
    assert reg and {i["region"] for i in reg} == {ctx["A_region"]}
    assert len(c.get("/api/institutions", headers=login(c)).json()) > len(reg)
    assert c.get("/api/institutions", headers={"Authorization": ""}).status_code == 401
