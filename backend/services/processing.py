"""Application processing service: document versioning, analysis persistence, pipeline runs, reviewer actions.

The pipeline code (backend/pipeline) is DB-free; this module is the only place that maps it onto the ORM.
Replacing a document creates a NEW version (old kept, is_current=False, supersedes_id set). Re-running the checks keeps
reviewer decisions for findings that are unchanged (matched by fingerprint) and resets findings tied to replaced documents.
"""
from __future__ import annotations

import hashlib
import importlib
import os
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app import models as M
from ..pipeline import rules as R
from ..pipeline.orchestrator import Pipeline
from ..pipeline.types import FieldValue
from . import audit, storage
from .clock import utcnow

MAX_UPLOAD_BYTES = int(os.environ.get("AICTE_MAX_UPLOAD", 25 * 1024 * 1024))
ACTIONS = {"accept": "accepted", "override": "overridden", "request_clarification": "clarification_requested"}
_pipe: Pipeline | None = None
_lock = threading.Lock()


class ProcessingError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def get_pipeline() -> Pipeline:
    global _pipe
    with _lock:
        if _pipe is None:
            _pipe = build_pipeline()
        return _pipe


def build_pipeline(**kw) -> Pipeline:
    kw.setdefault("sleep", time.sleep)
    kw.setdefault("backoff_base", float(os.environ.get("AICTE_BACKOFF_BASE", 0.05)))
    p = Pipeline(**kw)
    for mod in ("ocr_router", "authenticity"):  # registered independently; authenticity arrives in M7
        try:
            importlib.import_module(f"..pipeline.{mod}", package=__package__).register(p)
        except ImportError:
            pass
    return p


def set_pipeline(p: Pipeline | None) -> None:
    global _pipe
    _pipe = p


# ------------------------------------------------------------------ applications
def create_application(db: Session, institution_id: str, category: str, submission_date, actor: str, role: str | None = None,
                       app_id: str | None = None) -> M.Application:
    from ..pipeline.orchestrator import CONFIG
    if not (CONFIG / "checklists" / f"{category}.json").exists():
        raise ProcessingError(f"unknown category '{category}'", 422)
    inst = db.get(M.Institution, institution_id)
    if inst is None:
        raise ProcessingError(f"unknown institution '{institution_id}'", 404)
    if app_id is None:
        n = db.execute(select(M.Application.id)).scalars().all()
        nums = [int(x.split("-")[1]) for x in n if x.startswith("APP-") and x.split("-")[1].isdigit()]
        app_id = f"APP-{(max(nums) if nums else 0) + 1:04d}"
    app = M.Application(id=app_id, institution_id=institution_id, category=category, submission_date=submission_date,
                        region=inst.region, status="draft", created_by=actor)
    db.add(app)
    db.flush()
    audit.log(db, actor, "application.create", "application", app.id, {"category": category, "institution_id": institution_id}, role)
    return app


# ------------------------------------------------------------------ documents
def _field_rows(doc_id: int, fields: dict[str, FieldValue]) -> list[M.ExtractedField]:
    return [M.ExtractedField(document_id=doc_id, name=n, value={"v": f.value}, confidence=f.confidence, page=f.page, bbox=f.bbox,
                             method=f.method, raw=f.raw, label=f.label) for n, f in fields.items()]


def analyze_document(db: Session, doc: M.Document, data: bytes) -> R.DocCtx:
    ctx = get_pipeline().analyze_file(str(doc.id), doc.filename, data, doc.version)
    doc.doc_type = ctx.doc_type
    doc.classification = {k: v for k, v in ctx.classification.items() if k != "scores"} | {"scores": ctx.classification.get("scores")}
    doc.sha256 = ctx.sha256
    doc.page_count = ctx.page_count
    doc.pages_info = [{"page": p["page"], "width": p["width"], "height": p["height"]} for p in ctx.pages]
    doc.source = ctx.source
    doc.metadata_json = ctx.metadata
    doc.qr_json = ctx.qr
    doc.signature_json = ctx.signature
    doc.tamper_json = ctx.tamper
    doc.timings = {k: round(v, 4) for k, v in ctx.timings.items()}
    for old in list(doc.fields):
        db.delete(old)
    db.flush()
    for row in _field_rows(doc.id, ctx.fields):
        db.add(row)
    db.flush()
    db.expire(doc, ["fields"])  # relationship was loaded before the rows were added; force a reload
    return ctx


IMAGE_MAGIC = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpeg", b"II*\x00": "tiff", b"MM\x00*": "tiff"}


def image_to_pdf(data: bytes, filename: str) -> tuple[bytes, str]:
    """Wrap an uploaded PNG/JPEG/TIFF into a single-page, A4-width PDF so that every document flows through the same pipeline
    (page images are then OCR'd). The wrapped PDF is what is stored and hashed; the original filename is kept with a .pdf suffix."""
    import pymupdf
    kind = next((v for k, v in IMAGE_MAGIC.items() if data.startswith(k)), None)
    if kind is None:
        raise ProcessingError("unsupported file type: upload a PDF, PNG, JPEG or TIFF", 415)
    try:
        img = pymupdf.open(stream=data, filetype=kind)
        pix = img[0].get_pixmap()
        w, h = pix.width, pix.height
        img.close()
    except Exception as e:  # corrupt image
        raise ProcessingError(f"could not read image: {e}", 400)
    if w < 200 or h < 200:
        raise ProcessingError("image is too small to read (minimum 200x200 pixels)", 422)
    width = 595.0
    out = pymupdf.open()
    page = out.new_page(width=width, height=round(width * h / w, 2))
    page.insert_image(page.rect, stream=data)
    out.set_metadata({"producer": "AICTE validator image upload wrapper", "creator": "upload", "title": "", "author": ""})
    pdf = out.tobytes(deflate=True)
    out.close()
    base = os.path.splitext(os.path.basename(filename))[0] or "upload"
    return pdf, f"{base}.pdf"


def add_document(db: Session, app: M.Application, filename: str, data: bytes, actor: str, role: str | None = None,
                 replaces_id: int | None = None, uploaded_at=None) -> M.Document:
    if not data:
        raise ProcessingError("empty file", 400)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ProcessingError("file too large", 413)
    if data[:5] != b"%PDF-":
        data, filename = image_to_pdf(data, filename)  # raises ProcessingError(415) for unsupported types
    version, supersedes = 1, None
    if replaces_id is not None:
        old = db.get(M.Document, replaces_id)
        if old is None or old.application_id != app.id:
            raise ProcessingError("document to replace not found in this application", 404)
        if not old.is_current:
            raise ProcessingError("only the current version of a document can be replaced", 409)
        version, supersedes = old.version + 1, old.id
        old.is_current = False
    doc = M.Document(application_id=app.id, filename=os.path.basename(filename) or "upload.pdf", version=version, supersedes_id=supersedes,
                     is_current=True, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), uploaded_by=actor,
                     uploaded_at=uploaded_at or utcnow(), storage_path=storage.save(app.id, data))
    db.add(doc)
    db.flush()
    analyze_document(db, doc, data)
    audit.log(db, actor, "document.replace" if supersedes else "document.upload", "document", doc.id,
              {"application_id": app.id, "filename": doc.filename, "version": version, "supersedes_id": supersedes,
               "sha256": doc.sha256, "doc_type": doc.doc_type}, role)
    return doc


def doc_to_ctx(doc: M.Document) -> R.DocCtx:
    fields = {f.name: FieldValue(name=f.name, value=(f.value or {}).get("v"), confidence=f.confidence, page=f.page, bbox=f.bbox,
                                 method=f.method or "", raw=f.raw, label=f.label) for f in doc.fields}
    return R.DocCtx(document_id=str(doc.id), filename=doc.filename, doc_type=doc.doc_type or "unknown", classification=doc.classification or {},
                    fields=fields, pages=[], metadata=doc.metadata_json or {}, sha256=doc.sha256, qr=doc.qr_json or [],
                    source=doc.source or "text_layer", page_count=doc.page_count, signature=doc.signature_json,
                    tamper=doc.tamper_json or [], timings=doc.timings or {}, version=doc.version)


# ------------------------------------------------------------------ pipeline runs
def _fingerprint(f, doc: M.Document | None) -> str:
    return hashlib.sha1(f"{f.rule_id}|{doc.sha256 if doc else ''}|{f.details.get('doc_type')}|{f.message}".encode()).hexdigest()


def refresh_status(db: Session, app: M.Application) -> str:
    if app.last_run_id is None:
        app.status = "draft"
        return app.status
    active = db.execute(select(M.Finding).where(M.Finding.application_id == app.id, M.Finding.active.is_(True))).scalars().all()
    sts = {f.status for f in active}
    if "clarification_requested" in sts:
        app.status = "awaiting_clarification"
    elif sts & {"open", "manual_review"}:
        app.status = "in_scrutiny"
    else:
        app.status = "reviewed"
    return app.status


def run_pipeline(db: Session, app: M.Application, trigger: str, actor: str, role: str | None = None) -> M.PipelineRun:
    t0 = time.perf_counter()
    pipe = get_pipeline()
    cur = [d for d in app.documents if d.is_current]
    for d in cur:
        if d.doc_type is None:  # safety net: analyse anything not yet analysed
            analyze_document(db, d, storage.read(d.storage_path))
    docs = [doc_to_ctx(d) for d in cur]
    by_id = {str(d.id): d for d in cur}
    floor = min((d.id for d in cur), default=0)
    seen: dict = {}
    for sha, a_id, d_id in db.execute(select(M.Document.sha256, M.Document.application_id, M.Document.id)
                                      .where(M.Document.application_id != app.id, M.Document.id < floor).order_by(M.Document.id)).all():
        seen.setdefault(sha, (a_id, str(d_id)))
    run = M.PipelineRun(application_id=app.id, trigger=trigger, triggered_by=actor, started_at=utcnow())
    db.add(run)
    db.flush()
    inp = {"application_id": app.id, "category": app.category, "institution_id": app.institution_id,
           "submission_date": app.submission_date}
    findings, ctx = pipe.check_application(inp, docs, seen, now=utcnow(), id_prefix=f"-R{run.id}")

    for did, vr in ctx.verifications.items():
        db.add(M.Verification(application_id=app.id, document_id=int(did), run_id=run.id, ref_id=vr.ref_id, doc_type=vr.doc_type,
                              status=vr.status, reason=vr.reason, source=vr.source, timestamp=vr.timestamp,
                              raw_response=vr.raw_response, attempts=vr.attempts))
    prev = db.execute(select(M.Finding).where(M.Finding.application_id == app.id, M.Finding.active.is_(True))).scalars().all()
    prev_by_fp = {}
    for p in prev:
        if p.fingerprint:
            prev_by_fp.setdefault(p.fingerprint, p)
        p.active = False
    carried = 0
    for f in findings:
        doc = by_id.get(f.document_id) if f.document_id else None
        fp = _fingerprint(f, doc)
        status, carried_from = f.status, None
        old = prev_by_fp.get(fp)
        if old is not None and old.status in ("accepted", "overridden", "clarification_requested"):
            status, carried_from = old.status, old.id
            carried += 1
        db.add(M.Finding(id=f.id, application_id=app.id, document_id=int(f.document_id) if f.document_id else None, run_id=run.id,
                         rule_id=f.rule_id, severity=f.severity, source=f.source, message=f.message, evidence_text=f.evidence_text,
                         page=f.page, bbox=f.bbox, confidence=f.confidence, status=status, details=f.details, fingerprint=fp,
                         active=True, carried_from=carried_from, created_at=f.created_at))
    run.finished_at = utcnow()
    run.seconds = round(time.perf_counter() - t0, 4)
    run.n_findings = len(findings)
    run.details = {"carried_decisions": carried, "documents": len(docs)}
    app.last_run_id = run.id
    db.flush()
    refresh_status(db, app)
    audit.log(db, actor, "pipeline.run", "application", app.id,
              {"run_id": run.id, "trigger": trigger, "findings": len(findings), "carried_decisions": carried,
               "verifications": {k: v.status for k, v in ctx.verifications.items()}}, role)
    return run


# ------------------------------------------------------------------ reviewer actions
def apply_action(db: Session, finding: M.Finding, action: str, reason: str, actor: str, role: str | None = None) -> M.ReviewerAction:
    if action not in ACTIONS:
        raise ProcessingError(f"action must be one of {sorted(ACTIONS)}", 422)
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ProcessingError("a reason of at least 5 characters is required", 422)
    if not finding.active:
        raise ProcessingError("finding was superseded by a later pipeline run; act on the current finding", 409)
    old = finding.status
    finding.status = ACTIONS[action]
    ra = M.ReviewerAction(finding_id=finding.id, application_id=finding.application_id, actor=actor, actor_role=role, action=action,
                          reason=reason, resulting_status=finding.status)
    db.add(ra)
    db.flush()
    audit.log(db, actor, f"finding.{action}", "finding", finding.id,
              {"application_id": finding.application_id, "from": old, "to": finding.status, "reason": reason, "rule_id": finding.rule_id}, role)
    app = db.get(M.Application, finding.application_id)
    refresh_status(db, app)
    return ra
