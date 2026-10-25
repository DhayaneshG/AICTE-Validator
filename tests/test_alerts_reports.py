"""M10: config-driven alerts, dedup, escalation on a simulated clock, notifications (+MOCK email), CSV/PDF reports reconciling with the dashboard."""
import csv
import datetime as dt
import io

import fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import models as M, seed
from backend.app.db import SessionLocal, init_db
from backend.app.main import app
from backend.services import alerts as A
from backend.services.clock import clock, frozen
from tests.conftest import login

T0 = dt.datetime(2026, 3, 1, 9, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="module")
def env():
    init_db()
    db = SessionLocal()
    seed.seed_institutions(db)
    db.commit()
    seed.seed_applications(db, limit=8)
    seed.seed_users(db)
    db.commit()
    db.close()
    with TestClient(app) as c:
        c.headers.update(login(c))
        yield c


def _db():
    return SessionLocal()


def _tick(at):
    clock.set(at)
    db = _db()
    r = A.evaluate(db)
    db.commit()
    db.close()
    return r


def test_rules_are_config_driven_and_validated(tmp_path):
    cfg = A.load_rules()
    assert [r["id"] for r in cfg["rules"]][0] == "AL-HIGH-OPEN" and cfg["levels"][-1] == "admin_senior"
    bad = tmp_path / "r.json"
    bad.write_text('{"levels":["a","b","c"],"rules":[{"id":"X","condition":"nope","escalate_after_hours":[1,2]}]}')
    with pytest.raises(ValueError):
        A.load_rules(bad)


def test_raise_dedup_escalate_resolve_on_simulated_clock(env):
    c = env
    try:
        with frozen(T0):  # findings were created "now"; place the condition start at T0 for determinism
            db = _db()
            db.execute(M.Finding.__table__.update().values(created_at=T0))
            db.execute(M.Alert.__table__.delete())
            db.execute(M.Notification.__table__.delete())
            db.commit()
            highs = db.execute(select(M.Finding).where(M.Finding.active.is_(True), M.Finding.status == "open", M.Finding.severity == "high")).scalars().all()
            assert highs, "fixture needs open high findings"
            n_high = len(highs)
            db.close()
        r1 = _tick(T0 + dt.timedelta(minutes=1))
        assert r1["raised"] >= n_high
        r2 = _tick(T0 + dt.timedelta(minutes=2))  # idempotent: same condition, no duplicates
        assert r2["raised"] == 0 and r2["escalated"] == 0
        db = _db()
        high_alerts = db.execute(select(M.Alert).where(M.Alert.rule_id == "AL-HIGH-OPEN")).scalars().all()
        assert len(high_alerts) == n_high and all(a.level == "scrutiny_officer" for a in high_alerts)
        assert len({a.dedup_key for a in high_alerts}) == n_high
        # medium alerts are NOT due yet (raise_after 72 h)
        assert db.query(M.Alert).filter(M.Alert.rule_id == "AL-MEDIUM-OPEN").count() == 0
        db.close()
        # 25 h -> escalated to regional; 73 h -> admin; medium alerts due at 72 h
        assert _tick(T0 + dt.timedelta(hours=23, minutes=59))["escalated"] == 0
        assert _tick(T0 + dt.timedelta(hours=24, minutes=1))["escalated"] == n_high
        db = _db()
        assert {a.level for a in db.execute(select(M.Alert).where(M.Alert.rule_id == "AL-HIGH-OPEN")).scalars()} == {"regional_officer"}
        db.close()
        r = _tick(T0 + dt.timedelta(hours=72, minutes=1))
        db = _db()
        assert {a.level for a in db.execute(select(M.Alert).where(M.Alert.rule_id == "AL-HIGH-OPEN")).scalars()} == {"admin_senior"}
        assert db.query(M.Alert).filter(M.Alert.rule_id == "AL-MEDIUM-OPEN").count() > 0
        # escalation is audit-logged with from/to
        esc = db.execute(select(M.AuditLog).where(M.AuditLog.action == "alert.escalate")).scalars().all()
        assert esc and {(e.details["from"], e.details["to"]) for e in esc} >= {("scrutiny_officer", "regional_officer"), ("regional_officer", "admin_senior")}
        db.close()
    finally:
        clock.reset()


def test_notifications_follow_the_escalation_path_and_include_mock_email(env):
    c = env
    db = _db()
    notes = db.execute(select(M.Notification)).scalars().all()
    db.close()
    roles = {n.role for n in notes}
    assert {"scrutiny_officer", "regional_officer", "admin_senior"} <= roles
    assert {n.channel for n in notes} == {"in_app", "email_mock"}
    # an admin sees only admin-targeted notifications; MOCK email rows flagged
    j = c.get("/api/notifications").json()
    assert j["items"] and all(i["to_role"] == "admin_senior" for i in j["items"])
    assert any(i["mock"] for i in j["items"]) and j["unread"] > 0
    assert c.post("/api/notifications/read").json()["marked"] > 0
    assert c.get("/api/notifications").json()["unread"] == 0
    # a scrutiny officer only sees their region's alerts-level notifications
    db = _db()
    reg = db.query(M.Alert).first().application_id
    region = db.get(M.Application, reg).region
    db.close()
    h = login(c, f"scrutiny.{region.lower()}")
    mine = c.get("/api/notifications", headers=h).json()["items"]
    assert mine and all(i["to_role"] == "scrutiny_officer" and i["to_region"] == region for i in mine)


def test_ack_stops_escalation_and_action_resolves(env):
    c = env
    try:
        clock.set(T0 + dt.timedelta(days=10))
        db = _db()
        db.execute(M.Alert.__table__.delete())
        db.execute(M.Finding.__table__.update().values(created_at=T0 + dt.timedelta(days=10)))  # conditions start 'now'
        db.commit()
        db.close()
        _tick(T0 + dt.timedelta(days=10))  # re-raise everything: levels start from the bottom again
        db = _db()
        al = db.execute(select(M.Alert).where(M.Alert.rule_id == "AL-HIGH-OPEN")).scalars().all()
        assert al
        a0, a1 = al[0], al[1] if len(al) > 1 else None
        aid0, fid1 = a0.id, (a1.finding_id if a1 else None)
        region = db.get(M.Application, a0.application_id).region
        level_after = {a.id: a.level for a in al}
        db.close()
        h = login(c, f"scrutiny.{region.lower()}")
        assert c.post(f"/api/alerts/{aid0}/acknowledge", headers=h).json()["status"] == "acknowledged"
        # alerts list is scoped to region for scrutiny officers; institution users cannot list alerts
        items = c.get("/api/alerts", headers=h).json()["items"]
        assert items and all(i["region"] == region for i in items)
        assert c.get("/api/alerts", headers=login(c, "inst.a")).status_code == 403
        # acknowledged alert stays at its level when time passes; unacknowledged one escalates
        db = _db()
        due = db.get(M.Alert, aid0).due_at
        db.close()
        base = due if due.tzinfo else due.replace(tzinfo=dt.timezone.utc)
        _tick(base + dt.timedelta(hours=30))
        db = _db()
        assert db.get(M.Alert, aid0).level == level_after[aid0] == "scrutiny_officer"
        others = [a for a in db.execute(select(M.Alert).where(M.Alert.rule_id == "AL-HIGH-OPEN", M.Alert.id != aid0)).scalars() if a.status == "open"]
        assert any(a.level == "regional_officer" for a in others)
        db.close()
        # reviewer decision clears the condition -> alert resolves on the next tick
        db = _db()
        f = db.get(M.Finding, db.get(M.Alert, aid0).finding_id)
        app_region = db.get(M.Application, f.application_id).region
        fid = f.id
        db.close()
        r = c.post(f"/api/findings/{fid}/actions", headers=login(c, f"scrutiny.{app_region.lower()}"), json={"action": "accept", "reason": "checked with original"})
        assert r.status_code == 201
        res = _tick(base + dt.timedelta(hours=31))
        assert res["resolved"] >= 1
        db = _db()
        assert db.get(M.Alert, aid0).status == "resolved" and db.get(M.Alert, aid0).resolved_at is not None
        db.close()
    finally:
        clock.reset()


def test_clarification_overdue_notifies_the_institution(env):
    c = env
    db = _db()
    aid = db.execute(select(M.Alert.application_id)).scalars().first()
    ap = db.get(M.Application, aid)
    fid = db.execute(select(M.Finding.id).where(M.Finding.application_id == aid, M.Finding.active.is_(True), M.Finding.status.in_(("open", "manual_review")))).scalars().first()
    region, inst = ap.region, ap.institution_id
    db.close()
    if fid is None:
        pytest.skip("no actionable finding on the chosen application")
    try:
        clock.set(T0 + dt.timedelta(days=20))
        r = c.post(f"/api/findings/{fid}/actions", headers=login(c, f"scrutiny.{region.lower()}"), json={"action": "request_clarification", "reason": "please explain"})
        assert r.status_code == 201
        res = _tick(T0 + dt.timedelta(days=20, hours=169))
        assert res["raised"] >= 1
        db = _db()
        n = db.execute(select(M.Notification).where(M.Notification.role == "institution_user", M.Notification.institution_id == inst)).scalars().all()
        db.close()
        assert n and any("AL-CLARIF-OVERDUE" in x.subject for x in n)
    finally:
        clock.reset()


def test_reports_reconcile_with_dashboard(env):
    c = env
    q = c.get("/api/applications").json()
    s = c.get("/api/reports/summary").json()
    assert s["findings"]["open_by_severity"] == {k: q["totals"][k] for k in ("high", "medium", "low")}
    assert s["findings"]["manual_review"] == q["totals"]["manual_review"]
    assert s["applications"]["total"] == q["count"]
    rows = list(csv.DictReader(io.StringIO(c.get("/api/reports/findings.csv").text)))
    assert len(rows) == s["findings"]["active_total"]
    for sev in ("high", "medium", "low"):
        assert sum(1 for r in rows if r["severity"] == sev and r["status"] == "open") == q["totals"][sev]
    assert sum(1 for r in rows if r["status"] == "manual_review") == q["totals"]["manual_review"]
    pdf = fitz.open(stream=c.get("/api/reports/summary.pdf").content, filetype="pdf")
    text = "\n".join(p.get_text() for p in pdf)
    assert "Advisory only" in text and "synthetic" in text
    t = q["totals"]
    assert f"high {t['high']}, medium {t['medium']}, low {t['low']}; manual review {t['manual_review']}" in text
    # per-application PDF
    aid = q["items"][0]["id"]
    ap = fitz.open(stream=c.get(f"/api/applications/{aid}/report.pdf").content, filetype="pdf")
    assert aid in ap[0].get_text() and "MOCK" in "\n".join(p.get_text() for p in ap)


def test_report_exports_are_role_gated_scoped_and_audited(env):
    c = env
    inst = login(c, "inst.a")
    for u in ("/api/reports/summary", "/api/reports/findings.csv", "/api/reports/summary.pdf"):
        assert c.get(u, headers=inst).status_code == 403
    assert c.get("/api/reports/summary").status_code == 200
    db = _db()
    region = db.query(M.Application).first().region
    db.close()
    h = login(c, f"scrutiny.{region.lower()}")
    rows = list(csv.DictReader(io.StringIO(c.get("/api/reports/findings.csv", headers=h).text)))
    assert rows and {r["region"] for r in rows} == {region}
    assert c.get("/api/reports/summary", headers=h).json()["scope"] == f"region {region}"
    assert c.get("/api/audit?action=report.export&limit=50").json()["total"] >= 3
    assert c.get("/api/audit/verify").json()["ok"]
