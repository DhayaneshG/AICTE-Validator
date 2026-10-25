"""Shared pydantic types for the pipeline (kept free of DB imports)."""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high"]
Source = Literal["rule", "anomaly", "tamper", "signature", "verification"]
FindingStatus = Literal["open", "accepted", "overridden", "clarification_requested", "manual_review"]
VerificationStatus = Literal["verified", "mismatch", "expired", "pending", "source_unavailable", "manual_review"]

ADVISORY = "Advisory indicator for human review - not proof of forgery or misconduct."


class FieldValue(BaseModel):
    name: str
    value: Any = None
    confidence: float = 0.0
    page: Optional[int] = None
    bbox: Optional[list[float]] = None
    method: str = "none"
    raw: Optional[str] = None
    label: Optional[str] = None


class Finding(BaseModel):
    id: str
    application_id: str
    document_id: Optional[str] = None
    rule_id: str
    severity: Severity
    source: Source
    message: str
    evidence_text: Optional[str] = None
    page: Optional[int] = None
    bbox: Optional[list[float]] = None
    confidence: float = 1.0
    status: FindingStatus = "open"
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    # extension (not part of the brief's core schema): defect hint, method, score, evidence crop path, etc.
    details: dict = Field(default_factory=dict)


class VerificationResult(BaseModel):
    status: VerificationStatus
    reason: str = ""
    ref_id: Optional[str] = None
    doc_type: Optional[str] = None
    source: str = "MOCK issuer registry"
    timestamp: str = ""
    raw_response: Optional[dict] = None
    attempts: list[dict] = Field(default_factory=list)
