"""Run the pipeline over the synthetic corpus. Apps are processed in application_id order with a shared
SHA-256 registry so duplicate detection only flags files that appeared in an EARLIER application."""
from __future__ import annotations

import copy

from backend.pipeline.orchestrator import Pipeline

from .common import doc_path, load_labels


def application_input(a: dict) -> dict:
    # NOTE: only what an institution would enter on the form; never the labels/defects.
    return {"application_id": a["application_id"], "category": a["category"], "institution_id": a["institution_id"],
            "submission_date": a["submission_date"]}


def analyze_all(pipe: Pipeline, labels: dict | None = None) -> dict:
    labels = labels or load_labels()
    out = {}
    for a in labels["applications"]:
        out[a["application_id"]] = [pipe.analyze_file(f"{a['application_id']}:{d['filename']}", d["filename"], doc_path(a, d).read_bytes())
                                    for d in a["documents"]]
    return out


def check_all(pipe: Pipeline, analyses: dict, labels: dict | None = None, thresholds: dict | None = None) -> dict:
    """Return {application_id: findings}. Fresh dup registry + issuer state each call."""
    labels = labels or load_labels()
    pipe.issuer.reset()
    old = pipe.thresholds
    if thresholds:
        pipe.thresholds = {**old, **thresholds}
    seen: dict = {}
    res = {}
    try:
        for a in sorted(labels["applications"], key=lambda x: x["application_id"]):
            findings, ctx = pipe.check_application(application_input(a), copy.copy(analyses[a["application_id"]]), seen)
            res[a["application_id"]] = {"findings": findings, "verifications": ctx.verifications}
    finally:
        pipe.thresholds = old
    return res
