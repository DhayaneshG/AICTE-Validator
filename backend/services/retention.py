"""Retention: purge the stored (encrypted) CONTENT of documents older than the retention period.

Metadata, the SHA-256, findings and the audit trail are kept (the hash lets a later copy still be matched as a duplicate).
Every purge is audit-logged. Period: AICTE_RETENTION_DAYS (default 365). Runs daily via the scheduler and on demand (admin).
"""
from __future__ import annotations

import datetime as dt
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app import models as M
from . import audit, storage
from .clock import utcnow


def retention_days() -> int:
    return int(os.environ.get("AICTE_RETENTION_DAYS", 365))


def purge_expired(db: Session, actor: str = "system.retention", role: str = "system", days: int | None = None, dry_run: bool = False) -> dict:
    days = retention_days() if days is None else days
    cutoff = utcnow() - dt.timedelta(days=days)
    docs = db.execute(select(M.Document).where(M.Document.purged_at.is_(None), M.Document.uploaded_at < cutoff)).scalars().all()
    purged, ids = 0, []
    for d in docs:
        ids.append(d.id)
        if dry_run:
            continue
        removed = storage.delete(d.storage_path) if d.storage_path else False
        d.storage_path, d.purged_at = None, utcnow()
        audit.log(db, actor, "document.purge", "document", d.id,
                  {"application_id": d.application_id, "sha256": d.sha256, "file_removed": removed, "retention_days": days}, role)
        purged += 1
    if not dry_run:
        db.flush()
    return {"retention_days": days, "cutoff": cutoff.isoformat(timespec="seconds"), "candidates": len(ids), "purged": purged,
            "document_ids": ids, "dry_run": dry_run}
