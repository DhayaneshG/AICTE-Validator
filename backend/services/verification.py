"""Verification history and retry of unresolved verifications (source unavailable / issuer confirmation pending).

Every pipeline run stores one Verification row per verifiable document (full history, never overwritten). The retry job finds
documents whose LATEST verification is unresolved and re-runs the application's checks with trigger "retry"; each retry and its
outcome is audit-logged. Issuer data is MOCK.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app import models as M
from . import audit, processing

UNRESOLVED = ("source_unavailable", "pending")


def latest_by_document(db: Session, app_id: str | None = None) -> dict[int, M.Verification]:
    q = select(M.Verification).where(M.Verification.document_id.is_not(None)).order_by(M.Verification.id)
    if app_id:
        q = q.where(M.Verification.application_id == app_id)
    out: dict[int, M.Verification] = {}
    for v in db.execute(q).scalars():
        out[v.document_id] = v
    return out


def unresolved(db: Session) -> list[M.Verification]:
    cur = {d for d in db.execute(select(M.Document.id).where(M.Document.is_current.is_(True))).scalars()}
    return [v for did, v in latest_by_document(db).items() if did in cur and v.status in UNRESOLVED]


def retry_unresolved(db: Session, actor: str = "system.scheduler", role: str = "system") -> dict:
    todo = unresolved(db)
    apps = sorted({v.application_id for v in todo})
    before = {v.document_id: v.status for v in todo}
    resolved = 0
    for aid in apps:
        app = db.get(M.Application, aid)
        processing.run_pipeline(db, app, "retry", actor, role)
        after = latest_by_document(db, aid)
        for v in todo:
            if v.application_id == aid:
                new = after.get(v.document_id)
                if new and new.status not in UNRESOLVED:
                    resolved += 1
                audit.log(db, actor, "verification.retry", "document", v.document_id,
                          {"application_id": aid, "ref_id": v.ref_id, "previous_status": before[v.document_id],
                           "new_status": new.status if new else None, "source": "MOCK issuer registry"}, role)
    audit.log(db, actor, "verification.retry_run", "system", None, {"unresolved_before": len(todo), "applications": len(apps), "resolved": resolved}, role)
    db.commit()
    return {"unresolved_before": len(todo), "applications": len(apps), "resolved": resolved}
