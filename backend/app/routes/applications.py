from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...mock.records_api import MockRecords
from ...pipeline.orchestrator import load_checklist
from ...services import processing as P
from ...services.clock import utcnow
from .. import models as M
from .. import schemas as S
from ..auth import STAFF, Actor, check_application, get_actor, in_scope, require, scope_applications
from ..db import get_db

router = APIRouter()
SEV_ORDER = {"low": 0, "medium": 1, "high": 2}


def _counts(db: Session, app_ids: list[str] | None = None) -> dict[str, dict]:
    q = select(M.Finding.application_id, M.Finding.severity, M.Finding.status).where(M.Finding.active.is_(True))
    if app_ids is not None:
        q = q.where(M.Finding.application_id.in_(app_ids))
    out: dict[str, dict] = defaultdict(lambda: {"high": 0, "medium": 0, "low": 0, "manual_review": 0, "clarification": 0, "open_total": 0})
    for a, sev, st in db.execute(q).all():
        c = out[a]
        if st == "open":
            c[sev] += 1
            c["open_total"] += 1
        elif st == "manual_review":
            c["manual_review"] += 1
        elif st == "clarification_requested":
            c["clarification"] += 1
    return out


def ser_queue_item(a: M.Application, counts: dict) -> dict:
    c = counts.get(a.id, {"high": 0, "medium": 0, "low": 0, "manual_review": 0, "clarification": 0, "open_total": 0})
    cur = [d for d in a.documents if d.is_current]
    return {"id": a.id, "institution_id": a.institution_id, "institution_name": a.institution.name, "region": a.region,
            "category": a.category, "submission_date": a.submission_date.isoformat(), "status": a.status, "documents": len(cur),
            "counts": c, "priority": 100 * c["high"] + 10 * c["medium"] + c["low"] + 5 * c["manual_review"],
            "age_days": (utcnow().date() - a.submission_date).days, "last_run_id": a.last_run_id, "updated_at": S.iso(a.updated_at)}


def get_app_or_404(db: Session, app_id: str, actor: Actor) -> M.Application:
    """Fetch an application the actor may see; out-of-scope rows answer 404 exactly like missing ones."""
    app = db.get(M.Application, app_id)
    if app is None:
        raise HTTPException(404, "application not found")
    return check_application(actor, app)


@router.get("/institutions")
def institutions(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    """Institutions the caller may create applications for (their own / their region / all)."""
    q = select(M.Institution).order_by(M.Institution.name)
    if actor.role == "institution_user":
        q = q.where(M.Institution.id == (actor.institution_id or "\0"))
    elif actor.role != "admin_senior":
        q = q.where(M.Institution.region == (actor.region or "\0"))
    return [{"id": i.id, "name": i.name, "region": i.region, "city": i.city, "state": i.state} for i in db.execute(q).scalars()]


@router.post("/applications", status_code=201)
def create_application(body: S.ApplicationCreate, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    inst = db.get(M.Institution, body.institution_id)
    if inst is not None and not in_scope(actor, inst.region, inst.id):
        raise HTTPException(403, "you may only create applications for institutions in your scope")
    try:
        app = P.create_application(db, body.institution_id, body.category, body.submission_date or utcnow().date(), actor.username, actor.role)
    except P.ProcessingError as e:
        raise HTTPException(e.status_code, str(e))
    db.commit()
    return ser_queue_item(app, {})


@router.get("/applications")
def list_applications(status: str | None = None, category: str | None = None, region: str | None = None, q: str | None = None,
                      min_severity: str | None = None, needs_manual_review: bool | None = None,
                      db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    qry = scope_applications(select(M.Application), actor)
    if status:
        qry = qry.where(M.Application.status == status)
    if category:
        qry = qry.where(M.Application.category == category)
    if region:
        qry = qry.where(M.Application.region == region)
    apps = db.execute(qry.order_by(M.Application.id)).scalars().all()
    counts = _counts(db, [a.id for a in apps])
    items = [ser_queue_item(a, counts) for a in apps]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["id"].lower() or ql in i["institution_name"].lower() or ql in i["institution_id"]]
    if min_severity:
        lv = SEV_ORDER[min_severity]
        items = [i for i in items if any(i["counts"][s] for s, v in SEV_ORDER.items() if v >= lv)]
    if needs_manual_review:
        items = [i for i in items if i["counts"]["manual_review"] > 0]
    items.sort(key=lambda i: (-i["priority"], i["id"]))
    totals = {"high": 0, "medium": 0, "low": 0, "manual_review": 0}
    for i in items:
        for k in totals:
            totals[k] += i["counts"][k]
    return {"items": items, "totals": totals, "count": len(items)}


@router.get("/applications/{app_id}")
def application_detail(app_id: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    app = get_app_or_404(db, app_id, actor)
    chk = load_checklist(app.category)
    docs = app.documents
    cur = [d for d in docs if d.is_current]
    present = {d.doc_type for d in cur}
    findings = db.execute(select(M.Finding).where(M.Finding.application_id == app_id).order_by(M.Finding.created_at, M.Finding.id)).scalars().all()
    vers = db.execute(select(M.Verification).where(M.Verification.application_id == app_id).order_by(M.Verification.id)).scalars().all()
    acts = db.execute(select(M.ReviewerAction).where(M.ReviewerAction.application_id == app_id).order_by(M.ReviewerAction.id)).scalars().all()
    runs = db.execute(select(M.PipelineRun).where(M.PipelineRun.application_id == app_id).order_by(M.PipelineRun.id)).scalars().all()
    rec = MockRecords().get(app.institution_id)
    item = ser_queue_item(app, _counts(db, [app_id]))
    return {
        "application": item,
        "institution": {"id": app.institution.id, "name": app.institution.name, "region": app.institution.region,
                        "city": app.institution.city, "state": app.institution.state},
        "mock_record": rec,  # MOCK AICTE record (labelled source)
        "checklist": {"id": chk["id"], "title": chk["title"], "required_documents": chk["required_documents"]},
        "missing_documents": [d for d in chk["required_documents"] if d not in present],
        "documents": [S.ser_document(d) for d in docs],
        "findings": [S.ser_finding(f) for f in findings],
        "verifications": [S.ser_verification(v) for v in vers],
        "reviewer_actions": [S.ser_action(a) for a in acts],
        "runs": [{"id": r.id, "trigger": r.trigger, "triggered_by": r.triggered_by, "started_at": S.iso(r.started_at), "seconds": r.seconds,
                  "n_findings": r.n_findings, "details": r.details} for r in runs],
    }


@router.post("/applications/{app_id}/documents", status_code=201)
async def upload_document(app_id: str, file: UploadFile = File(...), replaces_document_id: int | None = Form(None), run: str = Form("true"),  # string: FastAPI treats an absent bool Form field as False
                          db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    app = get_app_or_404(db, app_id, actor)
    data = await file.read()
    try:
        doc = P.add_document(db, app, file.filename or "upload.pdf", data, actor.username, actor.role, replaces_id=replaces_document_id)
        runinfo = None
        if run.lower() not in ("false", "0", "no"):
            r = P.run_pipeline(db, app, "replacement" if replaces_document_id else "initial", actor.username, actor.role)
            runinfo = {"run_id": r.id, "findings": r.n_findings, "carried_decisions": r.details.get("carried_decisions")}
    except P.ProcessingError as e:
        db.rollback()
        raise HTTPException(e.status_code, str(e))
    db.commit()
    return {"document": S.ser_document(doc), "run": runinfo}


@router.post("/applications/{app_id}/run")
def run_application(app_id: str, db: Session = Depends(get_db), actor: Actor = Depends(require(*STAFF))):
    app = get_app_or_404(db, app_id, actor)
    r = P.run_pipeline(db, app, "manual", actor.username, actor.role)
    db.commit()
    return {"run_id": r.id, "findings": r.n_findings, "seconds": r.seconds, "status": app.status}


@router.get("/applications/{app_id}/findings")
def application_findings(app_id: str, status: str | None = None, severity: str | None = None, source: str | None = None,
                         include_inactive: bool = False, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    get_app_or_404(db, app_id, actor)
    q = select(M.Finding).where(M.Finding.application_id == app_id)
    if not include_inactive:
        q = q.where(M.Finding.active.is_(True))
    if status:
        q = q.where(M.Finding.status == status)
    if severity:
        q = q.where(M.Finding.severity == severity)
    if source:
        q = q.where(M.Finding.source == source)
    return [S.ser_finding(f) for f in db.execute(q.order_by(M.Finding.id)).scalars()]


@router.get("/applications/{app_id}/verifications")
def application_verifications(app_id: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    get_app_or_404(db, app_id, actor)
    return [S.ser_verification(v) for v in db.execute(select(M.Verification).where(M.Verification.application_id == app_id).order_by(M.Verification.id)).scalars()]


@router.get("/applications/{app_id}/audit")
def application_audit(app_id: str, limit: int = 200, db: Session = Depends(get_db), actor: Actor = Depends(require(*STAFF))):
    get_app_or_404(db, app_id, actor)
    docs = [str(d.id) for d in db.execute(select(M.Document.id).where(M.Document.application_id == app_id)).scalars()]
    fids = [f for f in db.execute(select(M.Finding.id).where(M.Finding.application_id == app_id)).scalars()]
    rows = db.execute(select(M.AuditLog).order_by(M.AuditLog.id)).scalars().all()
    keep = [a for a in rows if (a.entity_type == "application" and a.entity_id == app_id)
            or (a.entity_type == "document" and a.entity_id in docs) or (a.entity_type == "finding" and a.entity_id in fids)
            or (a.details or {}).get("application_id") == app_id]
    return [S.ser_audit(a) for a in keep[-limit:]]


@router.get("/documents/{doc_id}/verifications")
def document_verifications(doc_id: int, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    d = db.get(M.Document, doc_id)
    if d is None:
        raise HTTPException(404, "document not found")
    get_app_or_404(db, d.application_id, actor)
    return [S.ser_verification(v) for v in db.execute(select(M.Verification).where(M.Verification.document_id == doc_id).order_by(M.Verification.id)).scalars()]


@router.post("/verifications/retry")
def retry_verifications(db: Session = Depends(get_db), actor: Actor = Depends(require("regional_officer", "admin_senior"))):
    """Manually trigger the retry of unresolved verifications (the scheduler does this periodically)."""
    from ...services import verification
    return verification.retry_unresolved(db, actor.username, actor.role)
