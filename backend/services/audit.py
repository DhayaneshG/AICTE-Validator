"""Append-only, hash-chained audit log.

hash_n = SHA-256( prev_hash || canonical_json({id, ts, actor, role, action, entity_type, entity_id, details}) ).
`verify_chain()` recomputes the whole chain and reports the first broken row. Updates/deletes of audit rows are blocked by
SQLAlchemy events and (SQLite) triggers. NOTE: a hash chain proves tampering *after the fact*; it does not stop someone who
controls the database file from rewriting the whole chain - production should anchor the head hash externally.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from ..app.models import AuditLog
from .clock import utcnow

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def compute_hash(prev_hash: str, row: dict) -> str:
    return hashlib.sha256((prev_hash + canonical(row)).encode()).hexdigest()


def _row_payload(r: AuditLog) -> dict:
    return {"id": r.id, "ts": r.ts, "actor": r.actor, "role": r.role, "action": r.action, "entity_type": r.entity_type,
            "entity_id": r.entity_id, "details": r.details}


def log(db: Session, actor: str, action: str, entity_type: str, entity_id: str | None = None, details: dict | None = None,
        role: str | None = None) -> AuditLog:
    last = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
    prev = last.hash if last else GENESIS
    row = AuditLog(ts=utcnow().isoformat(timespec="milliseconds"), actor=actor, role=role, action=action, entity_type=entity_type,
                   entity_id=str(entity_id) if entity_id is not None else None, details=details or {}, prev_hash=prev, hash="")
    db.add(row)
    db.flush()  # assigns id (part of the hashed payload)
    row.hash = compute_hash(prev, _row_payload(row))
    db.flush()
    return row


def verify_chain(db: Session) -> dict:
    prev = GENESIS
    n = 0
    for r in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars():
        n += 1
        if r.prev_hash != prev or r.hash != compute_hash(prev, _row_payload(r)):
            return {"ok": False, "checked": n, "broken_at": r.id, "head": None}
        prev = r.hash
    return {"ok": True, "checked": n, "broken_at": None, "head": prev}


# ---------------------------------------------------------------- append-only guards
@event.listens_for(AuditLog, "before_update")
def _no_update(mapper, connection, target):
    # the only permitted write after INSERT is filling in `hash` inside log() (hash == "" at that point)
    from sqlalchemy import inspect as sa_inspect
    st = sa_inspect(target)
    changed = {a.key for a in st.attrs if a.history.has_changes()}
    old_hash = st.attrs.hash.history.deleted
    if changed == {"hash"} and (not old_hash or old_hash[0] == ""):
        return
    raise PermissionError("audit_log is append-only")


@event.listens_for(AuditLog, "before_delete")
def _no_delete(mapper, connection, target):
    raise PermissionError("audit_log is append-only")


def install_append_only_guards(engine) -> None:
    """SQLite triggers blocking raw UPDATE/DELETE of audit rows (the ORM guards above cover SQLAlchemy sessions).
    Allows the single `hash = ''` -> value fill performed inside log()."""
    if engine.dialect.name != "sqlite":
        return  # Postgres: use equivalent triggers / revoke UPDATE,DELETE from the app role (see README)
    with engine.begin() as c:
        c.execute(text("""CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
            WHEN OLD.hash != '' BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;"""))
        c.execute(text("""CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;"""))
