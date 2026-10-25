from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...services import processing as P
from .. import models as M
from .. import schemas as S
from ..auth import STAFF, Actor, check_application, get_actor, require, scope_applications
from ..db import get_db

router = APIRouter()


@router.get("/findings")
def list_findings(status: str | None = None, severity: str | None = None, source: str | None = None, rule_id: str | None = None,
                  limit: int = 500, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    q = select(M.Finding).join(M.Application, M.Application.id == M.Finding.application_id).where(M.Finding.active.is_(True))
    q = scope_applications(q, actor)
    for col, v in ((M.Finding.status, status), (M.Finding.severity, severity), (M.Finding.source, source), (M.Finding.rule_id, rule_id)):
        if v:
            q = q.where(col == v)
    return [S.ser_finding(f) for f in db.execute(q.order_by(M.Finding.id).limit(limit)).scalars()]


@router.get("/findings/{finding_id}")
def get_finding(finding_id: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    f = db.get(M.Finding, finding_id)
    if f is None:
        raise HTTPException(404, "finding not found")
    check_application(actor, db.get(M.Application, f.application_id))
    acts = db.execute(select(M.ReviewerAction).where(M.ReviewerAction.finding_id == finding_id).order_by(M.ReviewerAction.id)).scalars()
    return {**S.ser_finding(f), "actions": [S.ser_action(a) for a in acts]}


@router.post("/findings/{finding_id}/actions", status_code=201)
def finding_action(finding_id: str, body: S.FindingAction, db: Session = Depends(get_db), actor: Actor = Depends(require(*STAFF))):
    f = db.get(M.Finding, finding_id)
    if f is None:
        raise HTTPException(404, "finding not found")
    check_application(actor, db.get(M.Application, f.application_id))
    try:
        ra = P.apply_action(db, f, body.action, body.reason, actor.username, actor.role)
    except P.ProcessingError as e:
        db.rollback()
        raise HTTPException(e.status_code, str(e))
    db.commit()
    return {"action": S.ser_action(ra), "finding": S.ser_finding(f)}
