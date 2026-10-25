"""ORM models. All data synthetic. Document versions are linked via supersedes_id; findings keep history via `active`."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..services.clock import utcnow
from .db import Base

ROLES = ["institution_user", "scrutiny_officer", "regional_officer", "admin_senior"]


class Institution(Base):
    __tablename__ = "institutions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # e.g. 1-12345678901
    name: Mapped[str] = mapped_column(String(255))
    region: Mapped[str] = mapped_column(String(8), index=True)
    state: Mapped[Optional[str]] = mapped_column(String(64))
    city: Mapped[Optional[str]] = mapped_column(String(64))
    approved_intake: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    full_name: Mapped[Optional[str]] = mapped_column(String(128))
    institution_id: Mapped[Optional[str]] = mapped_column(ForeignKey("institutions.id"), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # APP-0001
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    category: Mapped[str] = mapped_column(String(48))
    submission_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft|in_scrutiny|awaiting_clarification|reviewed
    region: Mapped[str] = mapped_column(String(8), index=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    institution: Mapped[Institution] = relationship()
    documents: Mapped[list["Document"]] = relationship(back_populates="application", order_by="Document.id")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    trigger: Mapped[str] = mapped_column(String(32))  # initial|replacement|manual|retry
    triggered_by: Mapped[Optional[str]] = mapped_column(String(64))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    seconds: Mapped[Optional[float]] = mapped_column(Float)
    n_findings: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    doc_type: Mapped[Optional[str]] = mapped_column(String(48))
    classification: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[Optional[int]] = mapped_column(ForeignKey("documents.id"), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[Optional[str]] = mapped_column(String(255))  # encrypted file (None after purge)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    pages_info: Mapped[list] = mapped_column(JSON, default=list)  # [{page,width,height}]
    source: Mapped[Optional[str]] = mapped_column(String(24))  # text_layer | ocr | no_text
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    qr_json: Mapped[list] = mapped_column(JSON, default=list)
    signature_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    tamper_json: Mapped[list] = mapped_column(JSON, default=list)
    timings: Mapped[dict] = mapped_column(JSON, default=dict)
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(64))
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    purged_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    application: Mapped[Application] = relationship(back_populates="documents")
    fields: Mapped[list["ExtractedField"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    name: Mapped[str] = mapped_column(String(48))
    value: Mapped[Optional[dict]] = mapped_column(JSON)  # {"v": ...} (keeps int/str types)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    page: Mapped[Optional[int]] = mapped_column(Integer)
    bbox: Mapped[Optional[list]] = mapped_column(JSON)
    method: Mapped[Optional[str]] = mapped_column(String(32))
    raw: Mapped[Optional[str]] = mapped_column(Text)
    label: Mapped[Optional[str]] = mapped_column(String(64))
    document: Mapped[Document] = relationship(back_populates="fields")


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("documents.id"), nullable=True, index=True)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=True)
    rule_id: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[str] = mapped_column(String(8), index=True)
    source: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    evidence_text: Mapped[Optional[str]] = mapped_column(Text)
    page: Mapped[Optional[int]] = mapped_column(Integer)
    bbox: Mapped[Optional[list]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)  # False once superseded by a later run
    carried_from: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Verification(Base):
    __tablename__ = "verifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("documents.id"), nullable=True, index=True)
    run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ref_id: Mapped[Optional[str]] = mapped_column(String(64))
    doc_type: Mapped[Optional[str]] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), index=True)
    reason: Mapped[Optional[str]] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64), default="MOCK issuer registry")
    timestamp: Mapped[Optional[str]] = mapped_column(String(40))
    raw_response: Mapped[Optional[dict]] = mapped_column(JSON)
    attempts: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewerAction(Base):
    __tablename__ = "reviewer_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[Optional[str]] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(32))  # accept | override | request_clarification
    reason: Mapped[str] = mapped_column(Text)
    resulting_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_alert_dedup"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[Optional[str]] = mapped_column(ForeignKey("applications.id"), nullable=True, index=True)
    finding_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    document_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rule_id: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(8), default="medium")
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | acknowledged | resolved
    level: Mapped[str] = mapped_column(String(32), default="scrutiny_officer")  # current escalation level
    dedup_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    due_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # when the condition first held
    escalated_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    institution_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="in_app")  # in_app | email_mock
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    alert_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    application_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """Append-only, hash-chained. Never updated or deleted (guarded by ORM events and, on SQLite, triggers)."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(64))
    role: Mapped[Optional[str]] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
