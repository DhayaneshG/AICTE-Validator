"""Alert engine: config-driven detection, de-duplication, time-based escalation, in-app notifications and a MOCK email channel.

`evaluate(db)` (idempotent) raises alerts for conditions that now hold, re-opens resolved ones that recur, resolves those that
stopped holding, and escalates open alerts through scrutiny_officer -> regional_officer -> admin_senior as time passes.
It uses services.clock, so tests and eval/alert_eval.py drive it with a simulated clock.
MOCK: the 'email' channel only writes a Notification row (channel=email_mock). Nothing is ever sent.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app import models as M
from . import audit
from .clock import utcnow

log = logging.getLogger("aicte.alerts")
CONFIG = Path(__file__).resolve().parents[1] / "config" / "alert_rules.json"
SYSTEM = "system.alerts"


def load_rules(path: Path | None = None) -> dict:
    cfg = json.loads((path or CONFIG).read_text())
    for r in cfg["rules"]:
        if r["condition"] not in CONDITIONS:
            raise ValueError(f"alert rule {r['id']}: unknown condition '{r['condition']}'")
        if len(r["escalate_after_hours"]) != len(cfg["levels"]) - 1:
            raise ValueError(f"alert rule {r['id']}: needs {len(cfg['levels']) - 1} escalation thresholds")
    return cfg


@dataclass
class Instance:
    """One concrete occurrence of a condition."""
    key: str
    application_id: str
    since: dt.datetime
    message: str
    finding_id: str | None = None
    document_id: int | None = None
    extra: dict = field(default_factory=dict)


def _aware(d: dt.datetime) -> dt.datetime:
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------- condition detectors
def _first_seen(db: Session, f: M.Finding) -> dt.datetime:
    """A re-run replaces the Finding row but keeps its fingerprint: the clock starts at the EARLIEST row with that fingerprint."""
    q = select(M.Finding.created_at).where(M.Finding.application_id == f.application_id, M.Finding.fingerprint == f.fingerprint) if f.fingerprint else None
    ts = [_aware(t) for t in db.execute(q).scalars()] if q is not None else []
    return min(ts + [_aware(f.created_at)])


def cond_finding_open(db: Session, params: dict) -> list[Instance]:
    sev = params.get("severity", ["high"])
    fs = db.execute(select(M.Finding).where(M.Finding.active.is_(True), M.Finding.status == "open", M.Finding.severity.in_(sev))).scalars().all()
    return [Instance(f"finding:{f.id}", f.application_id, _first_seen(db, f), f"[{f.severity}] {f.rule_id}: {f.message}", finding_id=f.id, document_id=f.document_id) for f in fs]


def cond_manual_review_stale(db: Session, params: dict) -> list[Instance]:
    fs = db.execute(select(M.Finding).where(M.Finding.active.is_(True), M.Finding.status == "manual_review")).scalars().all()
    return [Instance(f"finding:{f.id}", f.application_id, _first_seen(db, f), f"Manual review pending - {f.rule_id}: {f.message}", finding_id=f.id, document_id=f.document_id) for f in fs]


def cond_verification_unresolved(db: Session, params: dict) -> list[Instance]:
    out = []
    rows = db.execute(select(M.Verification).where(M.Verification.document_id.isnot(None)).order_by(M.Verification.id)).scalars().all()
    by_doc: dict[int, list[M.Verification]] = {}
    for v in rows:
        by_doc.setdefault(v.document_id, []).append(v)
    for did, vs in by_doc.items():
        if vs[-1].status not in ("source_unavailable", "pending"):
            continue
        start = vs[-1]
        for v in reversed(vs):  # start of the current unresolved streak
            if v.status in ("source_unavailable", "pending"):
                start = v
            else:
                break
        doc = db.get(M.Document, did)
        if doc is None or not doc.is_current:
            continue
        out.append(Instance(f"document:{did}", vs[-1].application_id, _aware(start.created_at),
                            f"Verification of {vs[-1].doc_type or 'document'} {vs[-1].ref_id or ''} is still '{vs[-1].status}' (MOCK issuer)", document_id=did))
    return out


def cond_clarification_overdue(db: Session, params: dict) -> list[Instance]:
    out = []
    for a in db.execute(select(M.Application).where(M.Application.status == "awaiting_clarification")).scalars():
        t = db.execute(select(M.ReviewerAction.created_at).where(M.ReviewerAction.application_id == a.id, M.ReviewerAction.action == "request_clarification")
                       .order_by(M.ReviewerAction.id.desc()).limit(1)).scalar_one_or_none()
        out.append(Instance(f"application:{a.id}", a.id, _aware(t or a.updated_at), f"Clarification requested for {a.id} has not been answered"))
    return out


CONDITIONS: dict[str, Callable[[Session, dict], list[Instance]]] = {
    "finding_open": cond_finding_open,
    "manual_review_stale": cond_manual_review_stale,
    "verification_unresolved": cond_verification_unresolved,
    "clarification_overdue": cond_clarification_overdue,
}


# ---------------------------------------------------------------- notifications (in-app + MOCK email)
def _notify(db: Session, alert: M.Alert, app: M.Application, role: str, headline: str, now: dt.datetime) -> None:
    region = None if role == "admin_senior" else app.region
    inst = app.institution_id if role == "institution_user" else None
    subject = f"[{alert.severity.upper()}] {headline}: {alert.rule_id} - {app.id}"
    body = f"{alert.message}\n\nApplication {app.id} ({app.institution.name}). Advisory indicator for human review."
    for channel in ("in_app", "email_mock"):
        db.add(M.Notification(role=role, region=region, institution_id=inst, channel=channel, subject=subject, body=body,
                              alert_id=alert.id, application_id=app.id, created_at=now))


def _event(alert: M.Alert, now: dt.datetime, event: str, **kw) -> None:
    alert.details = {**alert.details, "history": [*alert.details.get("history", []), {"at": now.isoformat(timespec="seconds"), "event": event, **kw}]}


# ---------------------------------------------------------------- engine
def evaluate(db: Session, now: dt.datetime | None = None, cfg: dict | None = None) -> dict:
    now = _aware(now or utcnow())
    cfg = cfg or load_rules()
    levels = cfg["levels"]
    stats = {"raised": 0, "reopened": 0, "resolved": 0, "escalated": 0, "open": 0}
    for rule in cfg["rules"]:
        due_delta = dt.timedelta(hours=rule["raise_after_hours"])
        current: dict[str, Instance] = {i.key: i for i in CONDITIONS[rule["condition"]](db, rule.get("params", {}))}
        existing = {a.dedup_key: a for a in db.execute(select(M.Alert).where(M.Alert.rule_id == rule["id"])).scalars()}
        for key, inst in current.items():
            due = inst.since + due_delta
            if now < due:
                continue
            dedup = f"{rule['id']}:{inst.application_id}:{key}"
            a = existing.get(dedup)
            app = db.get(M.Application, inst.application_id)
            if a is None:
                a = M.Alert(application_id=inst.application_id, finding_id=inst.finding_id, document_id=inst.document_id, rule_id=rule["id"],
                            severity=rule["severity"], message=inst.message, status="open", level=levels[0], dedup_key=dedup, created_at=now, due_at=due,
                            details={"title": rule["title"], "history": []})
                db.add(a)
                db.flush()
                _event(a, now, "raised", level=levels[0])
                audit.log(db, SYSTEM, "alert.raise", "alert", a.id, {"rule_id": rule["id"], "application_id": inst.application_id, "dedup_key": dedup,
                                                                   "lag_minutes": round((now - due).total_seconds() / 60, 1)}, "system")
                _notify(db, a, app, levels[0], "Alert", now)
                if rule.get("notify_institution"):
                    _notify(db, a, app, "institution_user", "Action needed", now)
                stats["raised"] += 1
            elif a.status == "resolved":  # condition came back
                a.status, a.level, a.resolved_at, a.escalated_at, a.due_at, a.created_at = "open", levels[0], None, None, due, now
                _event(a, now, "reopened", level=levels[0])
                audit.log(db, SYSTEM, "alert.reopen", "alert", a.id, {"rule_id": rule["id"], "application_id": inst.application_id}, "system")
                _notify(db, a, app, levels[0], "Alert (recurred)", now)
                stats["reopened"] += 1
        for dedup, a in existing.items():  # resolve alerts whose condition no longer holds
            key = dedup.split(":", 2)[2]
            if a.status != "resolved" and key not in current:
                a.status, a.resolved_at = "resolved", now
                _event(a, now, "resolved")
                audit.log(db, SYSTEM, "alert.resolve", "alert", a.id, {"rule_id": rule["id"], "reason": "condition cleared"}, "system")
                stats["resolved"] += 1
        # escalation: time since the alert became due; acknowledged alerts are owned by someone and do not escalate
        for a in db.execute(select(M.Alert).where(M.Alert.rule_id == rule["id"], M.Alert.status == "open")).scalars():
            elapsed_h = (now - _aware(a.due_at)).total_seconds() / 3600
            target = sum(1 for h in rule["escalate_after_hours"] if elapsed_h >= h)
            cur = levels.index(a.level)
            if target > cur:
                old, a.level, a.escalated_at = a.level, levels[target], now
                _event(a, now, "escalated", from_level=old, to_level=levels[target])
                audit.log(db, SYSTEM, "alert.escalate", "alert", a.id, {"rule_id": rule["id"], "from": old, "to": levels[target], "application_id": a.application_id,
                                                                     "overdue_minutes": round((elapsed_h - rule["escalate_after_hours"][target - 1]) * 60, 1)}, "system")
                _notify(db, a, db.get(M.Application, a.application_id), levels[target], f"ESCALATED from {old}", now)
                stats["escalated"] += 1
    db.flush()
    stats["open"] = len(db.execute(select(M.Alert.id).where(M.Alert.status.in_(("open", "acknowledged")))).all())
    return stats


def acknowledge(db: Session, alert: M.Alert, actor: str, role: str, note: str = "") -> M.Alert:
    if alert.status == "resolved":
        raise ValueError("alert is already resolved")
    alert.status = "acknowledged"
    _event(alert, utcnow(), "acknowledged", by=actor)
    audit.log(db, actor, "alert.acknowledge", "alert", alert.id, {"rule_id": alert.rule_id, "application_id": alert.application_id, "note": note}, role)
    return alert
