"""Structured per-application validation report (JSON). History data is MOCK."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...pipeline.types import ADVISORY
from ...services import audit, reports as R
from .. import models as M
from .. import schemas as S
from ..auth import STAFF, Actor, require
from ..db import get_db
from .applications import get_app_or_404

router = APIRouter()
HIST = Path(__file__).resolve().parents[2] / "mock" / "scrutiny_history.json"


def scrutiny_history(inst_id: str) -> dict:
    d = json.loads(HIST.read_text())["institutions"].get(inst_id, [])
    return {"source": "MOCK scrutiny history (synthetic)", "mock": True, "entries": d}


@router.get("/applications/{app_id}/report")
def report(app_id: str, db: Session = Depends(get_db), actor: Actor = Depends(require(*STAFF))):
    app = get_app_or_404(db, app_id, actor)
    fs = db.execute(select(M.Finding).where(M.Finding.application_id == app_id, M.Finding.active.is_(True)).order_by(M.Finding.id)).scalars().all()
    by_sev = {s: sum(1 for f in fs if f.severity == s) for s in ("high", "medium", "low")}
    by_status: dict[str, int] = {}
    for f in fs:
        by_status[f.status] = by_status.get(f.status, 0) + 1
    vers = db.execute(select(M.Verification).where(M.Verification.application_id == app_id)).scalars().all()
    return {"application_id": app.id, "institution": app.institution.name, "category": app.category, "status": app.status,
            "advisory": ADVISORY, "summary": {"findings": len(fs), "by_severity": by_sev, "by_status": by_status},
            "documents": [{"id": d.id, "type": d.doc_type, "version": d.version, "sha256": d.sha256} for d in app.documents if d.is_current],
            "findings": [S.ser_finding(f) for f in fs], "verifications": [S.ser_verification(v) for v in vers],
            "scrutiny_history": scrutiny_history(app.institution_id)}


def _export(db, actor, what: str, **details):
    audit.log(db, actor.username, "report.export", "report", what, details, actor.role)
    db.commit()


@router.get("/reports/summary")
def summary(db: Session = Depends(get_db), actor: Actor = Depends(require(*STAFF))):
    return R.summary(db, actor)


@router.get("/reports/findings.csv")
def findings_csv(db: Session = Depends(get_db), actor: Actor = Depends(require(*STAFF))):
    data = R.findings_csv(db, actor)
    _export(db, actor, "findings.csv", scope=R._scope_label(actor), rows=data.count("\n") - 1)
    return Response(data, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="findings.csv"'})


@router.get("/reports/summary.pdf")
def summary_pdf(db: Session = Depends(get_db), actor: Actor = Depends(require(*STAFF))):
    data = R.summary_pdf(db, actor)
    _export(db, actor, "summary.pdf", scope=R._scope_label(actor))
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="validation-summary.pdf"'})


@router.get("/applications/{app_id}/report.pdf")
def application_report_pdf(app_id: str, db: Session = Depends(get_db), actor: Actor = Depends(require(*STAFF))):
    app = get_app_or_404(db, app_id, actor)
    data = R.application_pdf(db, app)
    _export(db, actor, f"{app_id}.pdf", application_id=app_id)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{app_id}-report.pdf"'})
