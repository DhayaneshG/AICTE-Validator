from __future__ import annotations

import json

from fastapi import APIRouter

from ...pipeline.orchestrator import CONFIG
from ...pipeline.types import ADVISORY

router = APIRouter()

MOCK_COMPONENTS = [
    {"name": "AICTE records retrieval", "note": "synthetic institution records (backend/mock/records.json)"},
    {"name": "Issuer verification service", "note": "synthetic registry + simulated outages (backend/mock/issuer_api.py)"},
    {"name": "Structured validation report data source", "note": "synthetic scrutiny history (backend/mock/scrutiny_history.json)"},
    {"name": "Signature trust root", "note": "synthetic CA used to sign demo PDFs (backend/mock/trust_root)"},
]


def categories() -> list[dict]:
    out = []
    for p in sorted((CONFIG / "checklists").glob("*.json")):
        c = json.loads(p.read_text())
        out.append({"id": c["id"], "title": c["title"], "required_documents": c["required_documents"], "rules": len(c["rules"])})
    return out


@router.get("/meta")
def meta():
    return {"advisory": ADVISORY, "mock_components": MOCK_COMPONENTS, "categories": categories(), "synthetic_data": True}


@router.get("/categories")
def get_categories():
    return categories()


@router.get("/aph-crosscheck")
def aph_crosscheck():
    """How the validator maps to the Approval Process Handbook (APH) document requirements (static, config-driven)."""
    return json.loads((CONFIG / "aph_crosscheck.json").read_text())
