"""One-command evaluation on the held-out TEST split.

    python -m eval.run_eval            # full run (OCR + tamper + simulations: ~15-20 min), writes eval/results.md
    python -m eval.run_eval --quick    # smoke run: 2 scans per level, 2-day alert simulation (numbers are NOT headline numbers)

Every number in eval/results.md is produced by this script. Thresholds were tuned on DEV only (eval/tune_thresholds.py, eval/tamper tuning);
the TEST split is only read here. Anything simulated is labelled MOCK / SIMULATED.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from backend.mock.issuer_api import MockIssuerAPI
from backend.pipeline.orchestrator import Pipeline, load_thresholds

from . import authenticity_eval, defect_eval, extract_eval, feature_audit, ocr_eval
from .common import apps, doc_path, load_labels
from .defect_map import score
from .runner import analyze_all, application_input, check_all

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval"
T0 = time.perf_counter()


def log(msg: str) -> None:
    print(f"[{time.perf_counter() - T0:6.0f}s] {msg}", file=sys.stderr, flush=True)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def pct(x, nd=1) -> str:
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def pct_ci(k, n) -> str:
    ci = wilson(k, n)
    return "n/a" if ci is None else f"{pct(k / n)} ({k}/{n}; 95% CI {pct(ci[0], 0)}-{pct(ci[1], 0)})"


def num(x, nd=2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def run_sub(mod: str, *args: str) -> dict:
    p = subprocess.run([sys.executable, "-m", mod, *args, "--json"], cwd=ROOT, capture_output=True, text=True)
    for line in p.stderr.splitlines()[-3:]:
        log(f"   {mod}: {line.strip()}")
    if p.returncode not in (0, 1):
        raise RuntimeError(f"{mod} failed: {p.stderr[-600:]}")
    return json.loads(p.stdout[p.stdout.index("{"):])


def main(argv: list[str]) -> int:
    if "--rerender" in argv:  # re-write results.md from the saved results.json (e.g. after FEATURES.md changed); recomputes nothing else
        R = json.loads((OUT / "out" / "results.json").read_text())
        R["features"] = feature_audit.counts()
        (OUT / "out" / "results.json").write_text(json.dumps(R, indent=1, default=str))
        (OUT / "results.md").write_text(render(R))
        print("re-rendered eval/results.md from eval/out/results.json", file=sys.stderr)
        return 0
    quick = "--quick" in argv
    labels = load_labels()
    R: dict = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "quick": quick, "split": "test"}
    test_apps = apps(labels, "test")
    dev_apps = apps(labels, "dev")
    R["corpus"] = {"apps_test": len(test_apps), "apps_dev": len(dev_apps), "docs_test": sum(len(a["documents"]) for a in test_apps),
                   "defects_test": dict(Counter(d["defect_type"] for a in test_apps for d in a["defects"])),
                   "defects_dev": dict(Counter(d["defect_type"] for a in dev_apps for d in a["defects"])),
                   "clean_apps_test": sum(a["label"] == "clean" for a in test_apps)}

    log("1/9 digital extraction + classification (TEST)")
    R["extract"] = extract_eval.run("test")

    log("2/9 per-defect precision/recall (TEST, with DEV for comparison)")
    pipe = Pipeline()
    analyses = analyze_all(pipe, labels)
    de = defect_eval.run("test", pipe, analyses, labels)
    R["defects_test_score"] = de["score"]
    R["defects_dev_score"] = score(labels, de["results"], "dev", labels["defect_types"])
    results = de["results"]

    log("3/9 name-threshold sensitivity")
    thr = load_thresholds()
    sens = []
    for t in (60, 70, 76, 80, 84, 88, 92, 96, 100):
        s = score(labels, check_all(pipe, analyses, labels, {"name_similarity_min": t}), "test", ["name_mismatch"])["per_defect_app"]["name_mismatch"]
        clean_fp = score(labels, check_all(pipe, analyses, labels, {"name_similarity_min": t}), "test", ["name_mismatch"])["clean_app_false_positives"].get("name_mismatch", 0)
        sens.append({"threshold": t, **s, "clean_fp": clean_fp})
    R["name_sensitivity"] = {"configured": thr["name_similarity_min"], "rows": sens}

    log("4/9 verification coverage + evidence coverage + processing time (TEST)")
    vcount, vreason = Counter(), Counter()
    ev_text = ev_loc = n_f = 0
    no_loc_rules = Counter()
    for a in test_apps:
        r = results[a["application_id"]]
        for v in r["verifications"].values():
            vcount[v.status] += 1
            if v.reason and v.status != "verified":
                vreason[f"{v.status}:{v.reason}"] += 1
        for f in r["findings"]:
            if f.status not in ("open", "manual_review"):
                continue
            n_f += 1
            ev_text += bool(f.evidence_text)
            located = f.page is not None and bool(f.bbox)
            ev_loc += located
            if not located:
                no_loc_rules[f.rule_id.split("-")[0] + "-" + f.rule_id.split("-")[1] if f.rule_id.count("-") >= 1 else f.rule_id] += 1
    outage = Pipeline(issuer=MockIssuerAPI(outage_all=True))
    out_res = check_all(outage, analyses, labels)
    ocount = Counter(v.status for a in test_apps for v in out_res[a["application_id"]]["verifications"].values())
    t_pipe = Pipeline()
    secs, ndocs = 0.0, 0
    for a in test_apps:
        t = time.perf_counter()
        docs = [t_pipe.analyze_file(f"{a['application_id']}:{d['filename']}", d["filename"], doc_path(a, d).read_bytes()) for d in a["documents"]]
        t_pipe.check_application(application_input(a), docs, {})
        secs += time.perf_counter() - t
        ndocs += len(docs)
    R["verification"] = {"statuses": dict(vcount), "non_verified_reasons": dict(vreason), "documents_verified": sum(vcount.values()),
                         "under_total_outage_MOCK": dict(ocount)}
    R["evidence"] = {"findings": n_f, "with_evidence_text": ev_text, "with_page_and_bbox": ev_loc, "rules_without_location": dict(no_loc_rules)}
    R["timing_digital"] = {"applications": len(test_apps), "documents": ndocs, "seconds_per_application": secs / len(test_apps), "seconds_per_document": secs / ndocs}

    log("5/9 scanned documents through OCR (TEST, 3 degradation levels) - slow, progress below")
    R["ocr"] = ocr_eval.run("test", (1, 2, 3), limit=2 if quick else None)

    log("6/9 signatures + tamper indicators (TEST)")
    R["signatures"] = authenticity_eval.run_signatures("test")
    R["signatures_all"] = authenticity_eval.run_signatures(None)
    R["tamper"] = authenticity_eval.run_tamper("test")

    log("7/9 alert timeliness (SIMULATED clock, subprocess)")
    R["alerts"] = run_sub("eval.alert_eval", "--days", "2" if quick else "9", "--tick-minutes", "5")

    log("8/9 RBAC matrix (subprocess)")
    R["rbac"] = run_sub("eval.rbac_eval")["summary"]

    R["features"] = feature_audit.counts()
    R["seconds_total"] = round(time.perf_counter() - T0)
    (OUT / "out").mkdir(exist_ok=True)
    (OUT / "out" / ("results_quick.json" if quick else "results.json")).write_text(json.dumps(R, indent=1, default=str))
    log("9/9 writing eval/results.md")
    (OUT / ("results_quick.md" if quick else "results.md")).write_text(render(R))
    print(f"wrote eval/{'results_quick.md' if quick else 'results.md'} in {R['seconds_total']} s", file=sys.stderr)
    return 0


def render(R: dict) -> str:
    L: list[str] = []
    w = L.append
    c = R["corpus"]
    w("# Evaluation results (TEST split)\n")
    w(f"Generated {R['generated']} by `python -m eval.run_eval`{' (QUICK smoke run - NOT headline numbers)' if R['quick'] else ''}. "
      "Every figure below was computed by that script; nothing is estimated by hand.\n")
    w("> **Read these numbers as a demonstration, not a validation.** All data is synthetic, generated from a few known templates "
      f"(seed 42). The TEST split is small ({c['apps_test']} applications, {c['docs_test']} documents), so intervals are wide. Thresholds were tuned on the DEV split only "
      f"({c['apps_dev']} applications). Findings are advisory indicators for human review.\n")
    w("## 1. Digital PDFs - extraction and classification\n")
    e = R["extract"]
    f_ok = sum(v["correct"] for v in e["field_accuracy_by_field"].values())
    w(f"- Classification accuracy: {pct_ci(round(e['classification_accuracy'] * e['docs']), e['docs'])}")
    w(f"- Field accuracy (exact match after whitespace normalisation): {pct_ci(f_ok, e['field_accuracy_n'])}")
    w(f"- Highlighted bounding box re-parses to the correct value: {pct(e['bbox_contains_value'])}")
    w(f"- QR decode: {pct_ci(round(e['qr_decode_accuracy'] * e['qr_n']), e['qr_n'])}\n")
    w("| Field | Correct | n | Accuracy |\n|---|---|---|---|")
    for k, v in e["field_accuracy_by_field"].items():
        w(f"| {k} | {v['correct']} | {v['n']} | {pct(v['acc'])} |")
    w("\n## 2. Scanned documents (OCR) - three synthetic degradation levels\n")
    w("Scans are generated from the same documents with Augraphy (L1 light, L2 medium, L3 heavy: noise, blur, skew, shading, JPEG). "
      "'Silent wrong' = wrong value that was NOT flagged for manual review (low confidence or missing) - the dangerous case.\n")
    w("| Level | Files | Pages | Field accuracy | CER | Classification | QR decode | Wrong, flagged for review | Wrong, silent | s/page |\n|---|---|---|---|---|---|---|---|---|---|")
    for lv, v in R["ocr"]["levels"].items():
        k = round(v["field_accuracy"] * v["fields_n"])
        w(f"| L{lv} | {v['files']} | {v['pages']} | {pct_ci(k, v['fields_n'])} | {pct(v['cer'])} | {pct(v['classification_accuracy'])} | "
          f"{pct(v['qr_decode_rate'])} ({v['qr_n']}) | {v['wrong_fields_flagged_for_review']} | {v['wrong_fields_silent']} | {num(v['seconds_per_page'])} |")
    w("")
    w("## 3. Per-defect precision / recall (application level, digital PDFs)\n")
    t, d = R["defects_test_score"], R["defects_dev_score"]
    w("| Defect | TEST P | TEST R | TP/FP/FN | (DEV P / R, tuned on) |\n|---|---|---|---|---|")
    for k, m in t["per_defect_app"].items():
        dm = d["per_defect_app"][k]
        w(f"| {k} | {num(m['precision'])} | {num(m['recall'])} | {m['tp']}/{m['fp']}/{m['fn']} | {num(dm['precision'])} / {num(dm['recall'])} |")
    w(f"\nClean-application false positives on TEST: {t['clean_app_false_positives'] or 'none'}. "
      f"Other medium/high findings on TEST that map to no injected defect: {t['other_hard_findings'] or 'none'}.\n")
    w(f"Defect instances in TEST: {c['defects_test']} (clean apps: {c['clean_apps_test']}). With ~{min(c['defects_test'].values()) if c['defects_test'] else 0}-{max(c['defects_test'].values()) if c['defects_test'] else 0} instances per type, a perfect score has a wide interval.\n")
    w("### Name-similarity threshold sensitivity (`name_mismatch`, TEST)\n")
    s = R["name_sensitivity"]
    w(f"Configured value (tuned on DEV): **{s['configured']}**.\n")
    w("| Threshold | Precision | Recall | F1 | TP/FP/FN | False positives on clean apps |\n|---|---|---|---|---|---|")
    for r in s["rows"]:
        mark = " (configured)" if r["threshold"] == s["configured"] else ""
        w(f"| {r['threshold']}{mark} | {num(r['precision'])} | {num(r['recall'])} | {num(r['f1'])} | {r['tp']}/{r['fp']}/{r['fn']} | {r['clean_fp']} |")
    w("\nThe configured value was fixed on DEV and is **not** changed after looking at TEST; this table is shown for transparency about how sensitive the result is. "
      "Where a higher threshold would have scored better on TEST, that is a real (if small-sample) sign that the DEV-tuned value does not transfer perfectly.\n")
    w("\n## 4. Signature validation (pyHanko, MOCK trust root)\n")
    sg, sa = R["signatures"], R["signatures_all"]
    w(f"- TEST split: {sg['correct']}/{sg['n']} statuses as expected. All 12 signed fixtures: {sa['correct']}/{sa['n']} (nothing was tuned; validation is deterministic).")
    for r in sa["rows"]:
        w(f"  - `{r['file']}` ({r['kind']}): expected {r['expected']}, got {r['got']}")
    w("\n## 5. Tamper indicators on scanned pages (heuristic, advisory)\n")
    tp = R["tamper"]
    n_t = sum(v["n"] for k, v in tp["per_class"].items() if k != "clean")
    n_c = tp["per_class"].get("clean", {}).get("n", 0)
    k_t = round(tp["tampered_flag_recall"] * n_t) if tp["tampered_flag_recall"] is not None else 0
    k_c = round(tp["clean_false_positive_rate"] * n_c) if tp["clean_false_positive_rate"] is not None else 0
    w(f"- Any-indicator recall on tampered scans: {pct_ci(k_t, n_t)}")
    w(f"- Precision: {num(tp['precision'])}")
    w(f"- **Clean-scan false-positive rate: {pct_ci(k_c, n_c)}**\n")
    w("| Class | n | Flagged | Localised on the edited region | Flagged at medium |\n|---|---|---|---|---|")
    for k, v in tp["per_class"].items():
        w(f"| {k} | {v['n']} | {v['flagged']} | {v['localised'] if k != 'clean' else '-'} | {v['flagged_medium']} |")
    w(f"\nDetector hits by class: {tp['detector_hits_by_class']}. Severity is capped at *medium* in code: these are indicators, not proof. Applies only to image-only pages.\n")
    w("## 6. Verification coverage (MOCK issuer registry)\n")
    v = R["verification"]
    w(f"Documents sent for verification on TEST: {v['documents_verified']}. Outcomes: {v['statuses']}.")
    if v["non_verified_reasons"]:
        w(f"Reasons for non-verified outcomes: {v['non_verified_reasons']}.")
    w(f"\nUnder a simulated TOTAL outage of the MOCK issuer (3 attempts with exponential backoff each): {v['under_total_outage_MOCK']} - the pipeline degrades to `source_unavailable` (routed to manual review), never to a silent pass.\n")
    w("## 7. Evidence on findings\n")
    ev = R["evidence"]
    w(f"Of {ev['findings']} open/manual-review findings on TEST (digital pipeline): evidence text on {pct_ci(ev['with_evidence_text'], ev['findings'])}; page + bounding box (highlightable) on {pct_ci(ev['with_page_and_bbox'], ev['findings'])}. "
      f"Findings without a location are by nature document-level (e.g. a missing document, a duplicate-file hash): {ev['rules_without_location']}.\n")
    w("## 8. Processing time\n")
    td = R["timing_digital"]
    w(f"- Digital PDFs, extraction + checks (single process, CPU): **{num(td['seconds_per_application'], 3)} s/application**, {num(td['seconds_per_document'], 3)} s/document ({td['applications']} apps, {td['documents']} docs).")
    for lv, vv in R["ocr"]["levels"].items():
        w(f"- Scans L{lv} (OCR + preprocessing): {num(vv['seconds_per_page'])} s/page.")
    w("\nNot load-tested; single-process timings on the build machine.\n")
    w("## 9. Alerts, escalation and timeliness (SIMULATED clock)\n")
    a = R["alerts"]
    w(f"Simulated {a['days']:g} days in {a['tick_minutes']:g}-minute scheduler ticks ({a['ticks']} ticks, {a['alerts']} alerts, 12 applications). Time is simulated, so detection lag is bounded by the tick by construction; "
      f"the point is that the engine meets that bound, never duplicates and respects acknowledgements.\n")
    w(f"- Detection lag (alert created - alert due): mean {a['detection_lag_all']['mean_min']} min, max {a['detection_lag_all']['max_min']} min; {a['within_one_tick']['detections']}/{a['detection_lag_all']['n']} within one tick.")
    w(f"- Escalation delay: mean {a['escalation_delay_all']['mean_min']} min, max {a['escalation_delay_all']['max_min']} min; {a['within_one_tick']['escalations']}/{a['escalation_delay_all']['n']} within one tick.")
    w(f"- Duplicate alerts: {a['duplicate_dedup_keys']}. Escalations after acknowledgement: {a['escalations_after_acknowledgement']}. Audit chain intact afterwards: {a['audit_chain_ok']}.")
    w(f"- Notifications written: {a['notifications']} (the e-mail channel is MOCK - nothing is sent).")
    w(f"- Engine compute: mean {a['mean_engine_ms_per_tick']} ms/tick (real time).\n")
    w("| Rule | Alerts | Resolved | Escalations | Max detection lag (min) | Max escalation delay (min) |\n|---|---|---|---|---|---|")
    for k, vv in a["per_rule"].items():
        w(f"| {k} | {vv['alerts']} | {vv['resolved']} | {vv['escalation_delay']['n']} | {vv['detection_lag']['max_min']} | {vv['escalation_delay']['max_min'] if vv['escalation_delay']['n'] else '-'} |")
    w("\n## 10. Access control\n")
    rb = R["rbac"]
    w(f"RBAC / row-isolation matrix (real HTTP requests against a throw-away database): **{rb['passed']}/{rb['cases']} cases pass ({pct(rb['pass_rate'])})**. "
      "Covers unauthenticated and forged tokens, wrong-role (403), other-institution and other-region objects (404), list scoping, and document views.\n")
    fe = R["features"]
    w("## 11. Capability audit\n")
    w(f"From FEATURES.md: {fe['total']} capabilities - built {fe['built']}, mocked {fe['mocked']}, planned {fe['planned']}, downgraded {fe['downgraded']}, partial {fe['partial']}, not started {fe['not_started']}.\n")
    w("## 12. Limitations (read before quoting any number)\n")
    for x in LIMITATIONS(R):
        w(f"- {x}")
    return "\n".join(L) + "\n"


def LIMITATIONS(R: dict) -> list[str]:
    ocr = R["ocr"]["levels"]
    silent = {lv: v["wrong_fields_silent"] for lv, v in ocr.items()}
    silent_txt = ", ".join("L%s %d" % (lv, n) for lv, n in silent.items())
    acc_txt = ", ".join("L%s %s" % (lv, pct(v["field_accuracy"])) for lv, v in ocr.items())
    tp = R["tamper"]
    return [
        "**Synthetic data only.** Documents come from a handful of templates with injected defects; real AICTE submissions vary far more. Perfect digital scores here say the rules work as written, not that they would on real files.",
        f"**Small test set.** {R['corpus']['apps_test']} test applications; see the confidence intervals. Do not extrapolate.",
        f"**Scanned documents:** field accuracy falls with degradation ({acc_txt}); wrong values that were NOT flagged for review: {silent_txt}. Pale labels are the main loss. OCR was tuned on DEV scans only.",
        f"**Tamper detection is heuristic and limited:** recall {pct(tp['tampered_flag_recall'])}, clean-scan false-positive rate {pct(tp['clean_false_positive_rate'])}. It is marked *downgraded* in FEATURES.md, capped at medium severity, and only runs on image-only pages. It cannot prove tampering.",
        "**Signatures:** validated against a synthetic (MOCK) trust root with synthetic keys; real PKI chains, revocation (OCSP/CRL), timestamps and Indian CCA roots are not exercised.",
        "**MOCK sources:** AICTE records, issuer verification API, scrutiny history, signature trust root and the e-mail channel are simulated. Real integrations will have different latency, schemas and failure modes.",
        "**Alert timeliness is simulated** (see section 9): it demonstrates logic, not production latency.",
        "**Security is demo-grade:** HS256 JWT without refresh/revocation or MFA; in-memory login lockout; encryption key from an environment variable/dev file instead of a KMS; SQLite; single process; no rate limiting beyond login; no penetration test.",
        "**No load or concurrency testing.** Background jobs run in-process (APScheduler); production would need a queue and workers.",
        "**Advisory only.** Every finding is an indicator for human review; none is proof of forgery or misconduct. The UI carries a persistent banner saying so.",
    ]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
