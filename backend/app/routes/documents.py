from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ...pipeline.extract import render_page_png
from ...services import audit, storage
from .. import models as M
from .. import schemas as S
from ..auth import Actor, check_application, get_actor
from ..db import get_db

router = APIRouter()


def _doc(db: Session, doc_id: int, actor: Actor) -> M.Document:
    d = db.get(M.Document, doc_id)
    if d is None:
        raise HTTPException(404, "document not found")
    check_application(actor, db.get(M.Application, d.application_id))
    return d


def _bytes(db: Session, d: M.Document) -> bytes:
    if d.purged_at is not None or not d.storage_path:
        raise HTTPException(410, "document content was purged under the retention policy")
    try:
        return storage.read(d.storage_path)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(500, f"stored document unavailable: {e}")


@router.get("/documents/{doc_id}")
def document_detail(doc_id: int, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    return S.ser_document(_doc(db, doc_id, actor))


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: int, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    d = _doc(db, doc_id, actor)
    data = _bytes(db, d)
    audit.log(db, actor.username, "document.view", "document", d.id, {"application_id": d.application_id, "what": "file", "version": d.version}, actor.role)
    db.commit()
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{d.filename}"'})


@router.get("/documents/{doc_id}/page/{page}.png")
def document_page(doc_id: int, page: int, dpi: int = 110, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    d = _doc(db, doc_id, actor)
    if not 1 <= page <= max(1, d.page_count):
        raise HTTPException(404, "page out of range")
    dpi = max(50, min(dpi, 220))
    png = render_page_png(_bytes(db, d), page, dpi)
    info = next((p for p in d.pages_info if p["page"] == page), {})
    audit.log(db, actor.username, "document.view", "document", d.id, {"application_id": d.application_id, "what": "page", "page": page, "version": d.version}, actor.role)
    db.commit()
    return Response(png, media_type="image/png", headers={"X-Page-Width": str(info.get("width", "")), "X-Page-Height": str(info.get("height", "")),
                                                          "Cache-Control": "no-store"})
