"""Tune thresholds on the DEV split ONLY and write backend/config/thresholds.json (with provenance).
python -m eval.tune_thresholds"""
from __future__ import annotations

import datetime as dt
import json
import statistics

from backend.pipeline.orchestrator import CONFIG, Pipeline, load_thresholds

from .common import load_labels
from .defect_map import score
from .runner import analyze_all, check_all


def plateau_median(vals):
    return statistics.median(vals)


def main():
    labels = load_labels()
    dev_labels = {**labels, "applications": [a for a in labels["applications"] if a["split"] == "dev"]}
    pipe = Pipeline()
    analyses = analyze_all(pipe, dev_labels)  # DEV documents only
    base = load_thresholds()

    def f1(thr, defect):
        res = check_all(pipe, analyses, dev_labels, thr)
        return score(dev_labels, res, "dev", [defect])["per_defect_app"][defect]["f1"] or 0.0

    # 1) name similarity threshold: maximise F1 of name_mismatch on DEV
    grid = list(range(50, 101, 2))
    scores = {t: f1({"name_similarity_min": t}, "name_mismatch") for t in grid}
    best = max(scores.values())
    name_t = plateau_median([t for t, s in scores.items() if s == best])
    # 2) metadata window: maximise F1 of metadata_inconsistent on DEV
    mgrid = [(a, b) for a in (0, 1, 2, 3, 5, 7, 10, 14, 20) for b in (14, 20, 25, 30, 35, 40, 45, 60)]
    mscores = {ab: f1({"metadata_days_before_max": ab[0], "metadata_days_after_max": ab[1]}, "metadata_inconsistent") for ab in mgrid}
    mbest = max(mscores.values())
    ties = [ab for ab, s in mscores.items() if s == mbest]
    a_t = plateau_median([a for a, _ in ties])
    b_t = plateau_median([b for _, b in ties])
    new = {**base, "name_similarity_min": name_t, "metadata_days_before_max": a_t, "metadata_days_after_max": b_t}
    new["provenance"] = {"tuned_on": "dev split only", "dev_apps": len(dev_labels["applications"]),
                         "objective": "app-level F1 per defect; plateau median", "name_f1_dev": round(best, 3),
                         "name_plateau": [min(t for t, s in scores.items() if s == best), max(t for t, s in scores.items() if s == best)],
                         "metadata_f1_dev": round(mbest, 3), "tuned_at": dt.date.today().isoformat() if False else "fixed-date-for-reproducibility"}
    (CONFIG / "thresholds.json").write_text(json.dumps(new, indent=2))
    print("name_similarity_min:", name_t, "(F1 %.3f on dev; plateau %s)" % (best, new["provenance"]["name_plateau"]))
    print("metadata window: before_max=%s after_max=%s (F1 %.3f on dev)" % (a_t, b_t, mbest))


if __name__ == "__main__":
    main()
