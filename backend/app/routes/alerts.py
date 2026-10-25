from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from ...services import alerts as A
from .. import models as M
from .. import schemas as S
from ..auth import STAFF, Actor, check_application, get_actor, require, scope_applications
from ..db import get_db

router = APIRouter()


@router.get("/alerts")
def list_alerts(status: str | None = None, level: str | None = None, severity: str | None = None, db: Session = Depends(get_db),
                actor: Actor = Depends(require(*STAFF))):
    q = select(M.Alert, M.Application).join(M.Application, M.Application.id == M.Alert.application_id)
    q = scope_applications(q, actor)
    for col, v in ((M.Alert.status, status), (M.Alert.level, level), (M.Alert.severity, severity)):
        if v:
            q = q.where(col == v)
    rows = db.execute(q.order_by(M.Alert.id.desc())).all()
    items = [S.ser_alert(a, app) for a, app in rows]
    return {"items": items, "count": len(items), "by_level": {lv: sum(1 for i in items if i["level"] == lv and i["status"] != "resolved") for lv in A.load_rules()["levels"]},
            "open": sum(1 for i in items if i["status"] != "resolved")}


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: int, db: Session = Depends(get_db), actor: Actor = Depends(require(*STAFF))):
    a = db.get(M.Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    app = check_application(actor, db.get(M.Application, a.application_id))
    try:
        A.acknowledge(db, a, actor.username, actor.role)
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return S.ser_alert(a, app)


@router.post("/alerts/evaluate")
def evaluate_now(db: Session = Depends(get_db), actor: Actor = Depends(require("regional_officer", "admin_senior"))):
    """Run the alert engine now (the scheduler does this every AICTE_ALERT_MINUTES)."""
    r = A.evaluate(db)
    db.commit()
    return r


def _mine(actor: Actor):
    if actor.role == "admin_senior":
        return M.Notification.role == "admin_senior"
    if actor.role == "institution_user":
        return and_(M.Notification.role == "institution_user", M.Notification.institution_id == actor.institution_id)
    return and_(M.Notification.role == actor.role, M.Notification.region == actor.region)


@router.get("/notifications")
def notifications(unread_only: bool = False, channel: str | None = None, limit: int = 100, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    q = select(M.Notification).where(_mine(actor))
    if unread_only:
        q = q.where(M.Notification.read.is_(False))
    if channel:
        q = q.where(M.Notification.channel == channel)
    unread = db.execute(select(func.count(M.Notification.id)).where(_mine(actor), M.Notification.read.is_(False), M.Notification.channel == "in_app")).scalar_one()
    rows = db.execute(q.order_by(M.Notification.id.desc()).limit(min(limit, 500))).scalars().all()
    return {"unread": unread, "items": [S.ser_notification(n) for n in rows]}


@router.post("/notifications/read")
def mark_read(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    n = db.execute(update(M.Notification).where(_mine(actor), M.Notification.read.is_(False)).values(read=True)).rowcount
    db.commit()
    return {"marked": n}
