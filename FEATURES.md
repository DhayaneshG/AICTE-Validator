# FEATURES.md - capability audit

Status values: `not-started` | `built` | `partial` | `mocked` | `planned` | `downgraded`.
Statuses are updated as milestones complete; the final counts are computed by `eval/feature_audit.py` from this table.

> **Count discrepancy in the brief:** the capability list in the build brief names 27 BUILT-target items, 4 MOCKED and 7 PLANNED = **38 rows**, while the brief states "26 built / 37 total". This table tracks all 38 listed rows. Final counts are reported against the real total.

| # | Capability | Target | Status | Milestone | Notes |
|---|-----------|--------|--------|-----------|-------|
| 1 | Category selection | built | built | M4 | M4: tested in tests/test_backend.py |
| 2 | Configurable checklists | built | built | M1 | Checklists are JSON rules {id, doc, check, severity, message}; the engine dispatches on the check name (tests prove rules can be added, removed or re-severitised via JSON). |
| 3 | Document upload | built | built | M4 | M4: tested in tests/test_backend.py |
| 4 | PDF text extraction | built | built | M2 | PyMuPDF words -> rows/cells with bbox per page; has_text_layer per page and per document. |
| 5 | OCR | built | built | M6 | M6: RapidOCR (printed English only) + deskew/flatten/faint-text preprocessing. TEST field accuracy 83.0% light / 60.3% medium / 49.6% heavy scans (eval/results.md); 7-11 wrong values per level are NOT flagged for review. Pale labels are often lost on degraded scans; low-confidence fields route to manual review. Quality drops sharply with degradation - limitation |
| 6 | Classification | built | built | M2 | Keyword/title-weighted scoring; unknown -> manual review. Digital PDFs only so far (OCR path M6). |
| 7 | Field extraction | built | built | M2 | Label-anchored extraction (same cell / right / below) + regex fallback for IDs; {value, confidence, page, bbox, method}. |
| 8 | Replacement/rectification with version history | built | built | M4/M6 | M4/M6: versioning, carry-over of reviewer decisions, scan/image upload and rectification loop (poor scan -> manual review -> replacement) tested |
| 9 | Missing-document check | built | built | M3 | R-MISS-* (rule engine). |
| 10 | Expired-document check | built | built | M3 | R-EXP-* vs application submission date. |
| 11 | Cross-document consistency | built | built | M3 | Name (RapidFuzz token_sort on normalised names) + identifier consistency (institution ID, letter ID). |
| 12 | Metadata anomaly | built | built | M3 | R-META: PDF CreationDate vs stated issue date (DEV-tuned window). |
| 13 | Duplicate detection | built | built | M3 | R-DUP: SHA-256 vs earlier applications and within application. |
| 14 | ELA/copy-move tamper indicators | built | downgraded | M7 | M7: heuristic indicators only (noise flat-region, ELA, copy-move; severity capped at medium; image-only pages). TEST (n=32): any-indicator recall 79.2% (19/24), precision 1.00, clean-scan FPR 0/8; copy-move recall is lowest (4/8). DEV had 12.5% clean FPR. Not a forgery detector; a trained model is planned (#33) |
| 15 | Signature validation | built | built | M7 | M7: pyHanko validation against the MOCK trust root; 12 signed PDFs (8 valid + 4 negatives) all classified as expected; no thresholds involved |
| 16 | QR verification | built | built | M3 | OpenCV QR decode + MOCK issuer lookup (verification statuses incl. retry/backoff). |
| 17 | Manual-review routing | built | built | M3 | Low-confidence/missing fields, unclassified files and unverifiable references -> manual_review; OCR confidence feeds field confidence |
| 18 | Verification history with retry | built | built | M8 | M8: every run stores a Verification row (attempts + backoff log); APScheduler job + POST /api/verifications/retry re-verify source_unavailable/pending; retries audit-logged; UI timeline. Tested with simulated total outage |
| 19 | Officer queue | built | built | M5 | M5: queue with severity counts, filters, priority ranking |
| 20 | Findings with highlighted evidence | built | built | M5/M8 | M5/M8: findings carry evidence text, page, bbox; viewer highlights it (plus copy-move source block, tamper indicators); signature evidence panel; MOCK badges |
| 21 | Reviewer actions | built | built | M4 | M4: tested in tests/test_backend.py |
| 22 | Audit trail | built | built | M4 | M4: tested in tests/test_backend.py |
| 23 | Automated alerts | built | built | M10 | M10: config-driven rules (backend/config/alert_rules.json), de-duplicated by dedup_key, re-opened if a condition recurs, resolved when it clears; APScheduler job every 5 min; in-app + MOCK e-mail notifications. Timeliness measured on a simulated clock (eval/alert_eval.py) |
| 24 | Escalation | built | built | M10 | M10: scrutiny -> regional -> admin by elapsed time since due; acknowledged alerts do not escalate; every escalation audit-logged with from/to. Tested on a simulated clock |
| 25 | Reports/export | built | built | M10 | M10: findings CSV, summary PDF, per-application PDF, JSON summary; scoped by role, audited, reconciled with dashboard counts in tests/test_alerts_reports.py |
| 26 | Role-based access control (incl. encryption at rest, access logging, retention, security tests) | built | built | M9 | M9: JWT + bcrypt login, 4 roles, institution/region row isolation (out-of-scope -> 404), Fernet encryption at rest (KMS is a stand-in), every document view audited, retention purge (daily job + admin endpoint), login lockout. tests/test_security.py + eval/rbac_eval.py (55-case matrix) |
| 27 | Evaluation framework | built | built | M11 | M11: one command (python -m eval.run_eval) reports TEST-split results to eval/results.md: digital + scan field accuracy, per-defect P/R, name-threshold sensitivity, tamper P/R + clean FPR, signatures, verification coverage, evidence %, timing, alert timeliness (simulated), RBAC pass rate, limitations |
| 28 | AICTE records retrieval | mocked | mocked | M1 | MOCK: backend/mock/records.json + records_api.py (synthetic). |
| 29 | Issuer verification service | mocked | mocked | M3 | MOCK: backend/mock/issuer_registry.json + issuer_api.py (with simulated outages/pending). |
| 30 | Structured validation report data source | mocked | mocked | M3 | MOCK scrutiny history in backend/mock/scrutiny_history.json, served in /api/applications/{id}/report |
| 31 | Signature trust root | mocked | mocked | M7 | MOCK: synthetic root CA (backend/mock/trust_root/root_ca.pem); keys are synthetic (data/synthetic/signing_keys) |
| 32 | Checklist admin UI | planned | planned | - | Not built |
| 33 | Trained forgery model | planned | planned | - | Not built |
| 34 | Real issuer integration | planned | planned | - | Not built |
| 35 | Infrastructure evidence upload | planned | planned | - | Not built |
| 36 | Vision-based infrastructure flags | planned | planned | - | Not built |
| 37 | Inspection recommendation | planned | planned | - | Not built |
| 38 | AICTE portal integration | planned | planned | - | Not built |

## Downgrades / partials

- **#14 ELA/copy-move tamper indicators - DOWNGRADED** from "built" to heuristic indicators: severity capped at medium, image-only pages only, copy-move recall 4/8 on TEST, localisation not guaranteed. Documented limits in DECISIONS #21 and eval/results.md section 5.
- **#5 OCR - built, but with a quality caveat:** field accuracy on TEST scans is 83.0% (light), 60.3% (medium), 49.6% (heavy); 7-11 wrong values per level are not flagged for manual review.
- **#1, #3 UI for category selection / upload:** built; institution users create applications and upload PDF/PNG/JPEG/TIFF from the queue page.
- Everything marked MOCK (#28-#31) is simulated by design; #32-#38 are planned and not built.

## Post-M11 additions
- APH cross-check page + `/api/aph-crosscheck` (3 covered / 5 partial / 8 not covered). Opt-in rules R-APH-DSC and R-APH-LEASE-TERM ship disabled; the capability counts above are unchanged.
- UI restyled to the AICTE website look (not an official site; no emblem).
