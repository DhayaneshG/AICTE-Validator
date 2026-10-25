"""Shared helpers for evaluation scripts. Labels are ground truth; the pipeline never reads them."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "data" / "synthetic"

TRUTH_KEY = {"institution_name": "institution_name_written"}


def load_labels() -> dict:
    return json.loads((SYN / "labels.json").read_text())


def doc_path(app: dict, doc: dict) -> Path:
    return SYN / "applications" / app["application_id"] / doc["filename"]


def apps(labels: dict, split: str | None = None) -> list[dict]:
    return [a for a in labels["applications"] if split in (None, "all", a["split"])]


def truth_value(doc: dict, field: str):
    return doc["fields"].get(TRUTH_KEY.get(field, field))


def norm_ws(s):
    return " ".join(str(s).split()) if s is not None else None
