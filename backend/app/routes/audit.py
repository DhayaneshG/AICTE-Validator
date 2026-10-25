from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...services import audit
from .. import models as M
from .. import schemas as S
from ..auth import Actor, require
from ..db import get_db

router = APIRouter()


@router.get("/audit")
def list_audit(entity_type: str | None = None, entity_id: str | None = None, action: str | None = None, actor_name: str | None = None,
               limit: int = 100, offset: int = 0, db: Session = Depends(get_db), actor: Actor = Depends(require("admin_senior"))):
    q = select(M.AuditLog)
    cnt = select(func.count(M.AuditLog.id))
    for col, v in ((M.AuditLog.entity_type, entity_type), (M.AuditLog.entity_id, entity_id), (M.AuditLog.action, action), (M.AuditLog.actor, actor_name)):
        if v:
            q, cnt = q.where(col == v), cnt.where(col == v)
    total = db.execute(cnt).scalar_one()
    rows = db.execute(q.order_by(M.AuditLog.id.desc()).limit(min(limit, 500)).offset(offset)).scalars().all()
    return {"total": total, "items": [S.ser_audit(a) for a in rows]}


@router.get("/audit/verify")
def verify(db: Session = Depends(get_db), actor: Actor = Depends(require("admin_senior"))):
    return audit.verify_chain(db)
