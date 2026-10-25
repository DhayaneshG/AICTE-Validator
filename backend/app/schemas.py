"""Pydantic request/response schemas and serializers."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from . import models as M


class ApplicationCreate(BaseModel):
    institution_id: str
    category: str
    submission_date: Optional[dt.date] = None


class FindingAction(BaseModel):
    action: str = Field(description="accept | override | request_clarification")
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        if len((v or "").strip()) < 5:
            raise ValueError("a reason of at least 5 characters is required")
        return v.strip()


class LoginRequest(BaseModel):
    username: str
    password: str


def iso(x):
    return x.isoformat() if x is not None else None


def ser_field(f: M.ExtractedField) -> dict:
    return {"name": f.name, "value": (f.value or {}).get("v"), "confidence": f.confidence, "page": f.page, "bbox": f.bbox,
            "method": f.method, "label": f.label, "raw": f.raw}


def ser_document(d: M.Document, with_fields: bool = True) -> dict:
    out = {"id": d.id, "application_id": d.application_id, "filename": d.filename, "doc_type": d.doc_type, "version": d.version,
           "supersedes_id": d.supersedes_id, "is_current": d.is_current, "sha256": d.sha256, "size_bytes": d.size_bytes,
           "page_count": d.page_count, "pages_info": d.pages_info, "source": d.source,
           "classification": {"doc_type": (d.classification or {}).get("doc_type"), "confidence": (d.classification or {}).get("confidence")},
           "metadata": d.metadata_json, "qr": d.qr_json, "signature": d.signature_json, "tamper": d.tamper_json,
           "uploaded_by": d.uploaded_by, "uploaded_at": iso(d.uploaded_at), "purged": d.purged_at is not None}
    if with_fields:
        out["fields"] = [ser_field(f) for f in d.fields]
    return out


def ser_finding(f: M.Finding) -> dict:
    return {"id": f.id, "application_id": f.application_id, "document_id": f.document_id, "rule_id": f.rule_id, "severity": f.severity,
            "source": f.source, "message": f.message, "evidence_text": f.evidence_text, "page": f.page, "bbox": f.bbox,
            "confidence": f.confidence, "status": f.status, "created_at": iso(f.created_at), "details": f.details, "active": f.active,
            "carried_from": f.carried_from, "run_id": f.run_id, "advisory": "Indicator for human review - not proof of forgery or misconduct."}


def ser_verification(v: M.Verification) -> dict:
    return {"id": v.id, "document_id": v.document_id, "run_id": v.run_id, "ref_id": v.ref_id, "doc_type": v.doc_type, "status": v.status,
            "reason": v.reason, "source": v.source, "timestamp": v.timestamp, "raw_response": v.raw_response, "attempts": v.attempts,
            "mock": "MOCK" in (v.source or "")}


def ser_action(a: M.ReviewerAction) -> dict:
    return {"id": a.id, "finding_id": a.finding_id, "actor": a.actor, "actor_role": a.actor_role, "action": a.action, "reason": a.reason,
            "resulting_status": a.resulting_status, "created_at": iso(a.created_at)}


def ser_audit(a: M.AuditLog) -> dict:
    return {"id": a.id, "ts": a.ts, "actor": a.actor, "role": a.role, "action": a.action, "entity_type": a.entity_type,
            "entity_id": a.entity_id, "details": a.details, "prev_hash": a.prev_hash, "hash": a.hash}


def ser_alert(a: M.Alert, app: M.Application | None = None) -> dict:
    return {"id": a.id, "application_id": a.application_id, "institution_name": app.institution.name if app else None, "region": app.region if app else None,
            "finding_id": a.finding_id, "document_id": a.document_id, "rule_id": a.rule_id, "title": (a.details or {}).get("title"), "severity": a.severity,
            "message": a.message, "status": a.status, "level": a.level, "created_at": iso(a.created_at), "due_at": iso(a.due_at),
            "escalated_at": iso(a.escalated_at), "resolved_at": iso(a.resolved_at), "history": (a.details or {}).get("history", [])}


def ser_notification(n: M.Notification) -> dict:
    return {"id": n.id, "channel": n.channel, "mock": n.channel == "email_mock", "subject": n.subject, "body": n.body, "alert_id": n.alert_id,
            "application_id": n.application_id, "read": n.read, "created_at": iso(n.created_at), "to_role": n.role, "to_region": n.region}
