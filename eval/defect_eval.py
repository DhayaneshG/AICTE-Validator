"""Per-defect precision/recall.  python -m eval.defect_eval --split dev"""
from __future__ import annotations

import argparse

from backend.pipeline.orchestrator import Pipeline

from .common import load_labels
from .defect_map import fmt, score
from .runner import analyze_all, check_all


def run(split: str, pipe: Pipeline | None = None, analyses: dict | None = None, labels: dict | None = None) -> dict:
    labels = labels or load_labels()
    pipe = pipe or Pipeline()
    analyses = analyses or analyze_all(pipe, labels)
    results = check_all(pipe, analyses, labels)
    return {"score": score(labels, results, split, labels["defect_types"]), "results": results, "analyses": analyses}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    a = ap.parse_args()
    print(fmt(run(a.split)["score"]))
