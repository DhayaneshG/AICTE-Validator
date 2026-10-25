# BUILD_STATE.md

## Status
- M0 Scaffold: **done**
- M1 Synthetic data: **done**
- M2 Digital-PDF pipeline: **done** (extract, classify, fields, QR; DEV field accuracy 100%, n=380; classification 100%, n=77; QR 33/33)
- M3 Validation: **done** (rules, anomaly, MOCK issuer API + verify w/ retry/backoff, orchestrator; DEV P/R = 1.00 for all 9 defects, 0 clean-app false positives; thresholds tuned on DEV only)
- M4 Backend product layer: **done** (models, hash-chained append-only audit, encrypted storage, versioning, finding carry-over, reviewer actions, routes, seed, report; actor is an X-Actor header until M9 JWT)
- M5 Frontend: **done** (queue with severity tiles + filters, detail, evidence panel, server-rendered page viewer with bbox overlay, reviewer-action modal, upload/replace, audit view with chain verification, persistent advisory banner, MOCK badges; checked headless with Playwright: no console errors, highlight and action flow work)
- M6 OCR + scans: **done** (171 synthetic scans = 57 PDFs x 3 levels; preprocess/OCR/router; image upload; rectification loop). DEV scan results (eval/out/ocr_dev.json, n=143 fields, 12 QR): L1 light field acc 85.3%, CER 9.6%, QR 12/12; L2 medium 51.0%, CER 20.8%, QR 12/12; L3 heavy 51.7%, CER 16.6%, QR 10/12; ~4.7 s/page. At L2/L3 most wrong fields are None/low-confidence -> manual review (silent wrong values: 9 / 5 / 3 at L1/L2/L3). Main loss: pale sidebar labels become unreadable. No TEST scan numbers yet.
- Tests: 60 expected (`python -m pytest -q`) (`python -m pytest -q`, ~2 min)

## What exists (new in M2-M3)
- backend/pipeline: types.py, extract.py (rows/cells+bbox, metadata, QR), classify.py, fields.py, rules.py (checklist engine), anomaly.py, verify.py, orchestrator.py (Pipeline.analyze_file / check_application / process_application)
- backend/mock: issuer_api.py (MOCK, simulated outage/pending), records_api.py (MOCK)
- backend/config/thresholds.json (DEV-tuned, with provenance)
- eval/: common, extract_eval, runner, defect_map, defect_eval, tune_thresholds, feature_audit
- FEATURES.md: statuses updated (10 built, 1 partial, 2 mocked, 7 planned, 18 not started of 38)

## Known issues / notes
- Name threshold is under-determined by DEV (plateau 50-100, chosen 75). See DECISIONS.md #12 (disclosure). TEST name_mismatch may under-perform; a sensitivity table will be reported as a diagnostic.
- Verification statuses seen in the dataset: verified, mismatch, pending only; expired/source_unavailable/manual_review are exercised by unit tests (source_unavailable + retry also by simulated-outage refs that resolve on retry).
- Test suite is slowish (~95 s) because eval fixtures re-analyse the corpus.

## M4 notes
- Seed: `python -m backend.app.seed` (~80 s; loads 46 MOCK institutions and 40 apps through the service layer). Env: AICTE_DB, AICTE_VAR, AICTE_FERNET_KEY.
- Pending-reference apps (APP-0027, APP-0028) correctly show a low R-VER-NOC finding although labelled clean.
- Bugs fixed: stale `doc.fields` relationship after analysis; FastAPI treats an absent bool Form as False (run flag is now a string).
- Upload is PDF-only until M6.

## M5 notes
- Frontend: frontend/src (api.js, App, Queue, Detail, Viewer, AuditView, ui); hash routing, no UI libs. `npm run build` -> frontend/dist, served by FastAPI; dist is shipped in the zip so run.sh needs no Node.
- Actor selector/login and role-aware UI arrive in M9.

## M6 notes
- OCR settings (GAP_FRAC 0.2, GAP_MULT 2.0, gamma 2.0) and the deskew method were chosen on DEV scans only.
- First deskew used a global Otsu threshold and collapsed on shaded scans (ground-truth check: skew estimate now within 0.5 deg at all levels).
- Scan degradation levels were recalibrated once (DECISIONS #17). R-META skipped for scans (#18). Image uploads wrapped to PDF (#19).
- Eval commands: `python -m eval.ocr_eval --split dev` (progress on stderr). TEST only in M11.

## M7 notes
- Signatures: data/generator/signing.py (MOCK root + 12 signed PDFs), backend/pipeline/signatures.py, policy in backend/config/signature_policy.json. DEV 6/6 as expected (all 12 as expected too; deterministic, nothing tuned).
- Tamper: data/generator/tamper.py (64 images, 16/class, 8 dev + 8 test), backend/pipeline/tamper.py, thresholds in thresholds.json "tamper" (DEV-tuned). DEV: recall 70.8% (flagged), precision 94.4%, clean FPR 12.5% (ELA). By class: copy_move flagged 5/8 localised 2/8; retype 6/8 loc 5/8; erase 6/8 loc 6/8. Medium+ indicators: clean FPR 0/8.
- authenticity.register(pipeline) adds both analyzers + R-SIG / R-TAMPER checks; app pipeline registers it (build_pipeline). Eval: `python -m eval.authenticity_eval --split dev`.
- TEST not evaluated yet.

## M8 notes
- backend/services/verification.py (latest-per-document, retry_unresolved), services/scheduler.py (APScheduler; AICTE_SCHEDULER=0 disables, AICTE_RETRY_MINUTES default 10), POST /api/verifications/retry, GET /api/documents/{id}/verifications. tests/conftest.py turns the scheduler off.
- Bug found by the integration test: pyHanko cannot run inside FastAPI's running event loop -> validation now runs in a worker thread.
- Tamper indicators capped to the top 2 per detector per page (queue readability).
- UI: document badges (scan/OCR, signature status, tamper count), signature evidence box, verification timeline with attempt/backoff chips, tamper + copy-move source boxes in the viewer.

## M9 notes
- backend/app/auth.py (JWT, bcrypt, require(roles), scope helpers), routes/auth_routes.py (/auth/login, /auth/me, /users, /admin/purge), services/retention.py + scheduler job, seed.seed_users (admin, regional.<reg>, scrutiny.<reg>, inst.a, inst.b; password Demo@1234).
- eval/rbac_eval.py: 55-case matrix (`python -m eval.rbac_eval`), 55/55 pass; tests/test_security.py (10 tests). Full suite 93 passed.
- UI: login page, token in sessionStorage, page images fetched as authenticated blobs, role-aware nav/buttons (institution users get no review controls; Audit tab admin only).
- inst.a = SWRO institution 1-55045808051, inst.b = ERO institution 1-30995987559 (deterministic from seed 42).

## M10 notes
- services/alerts.py (+config/alert_rules.json), services/reports.py, routes/alerts.py (alerts, notifications), reports endpoints (summary, findings.csv, summary.pdf, applications/{id}/report.pdf), scheduler job alert_engine (AICTE_ALERT_MINUTES, default 5).
- eval/alert_eval.py (simulated clock, 9 days, 5-min ticks, ~46 s) -> eval/out/alert_eval.json. Result: 18 alerts, detection lag max 4 min (<= tick), 30 escalations all within one tick, 0 duplicates, 0 escalations after ack, audit chain ok.
- UI: Alerts, Reports, Notifications (unread badge) pages. Full suite: 100 passed.

## M11 notes
- eval/run_eval.py (TEST split; --quick smoke; --rerender) -> eval/results.md + eval/out/results.json (full run 769 s). README.md written (6-command quick start, demo script, Mermaid architecture, MOCK list, demo logins, headline numbers).
- Added GET /api/institutions + 'New application' UI so institution users can submit applications.
- FEATURES final: built 26, mocked 4, planned 7, downgraded 1 (#14), not-started 0 (38 rows). Full suite: 101 passed.
- Remaining: package zip, unzip -t, fresh-unzip smoke test (install, seed, start, /health, demo login), SendUserFile, final summary.

## Next step
- Packaging + smoke test only. Nothing else outstanding.
Post-M11: UI restyle + APH cross-check done; see DECISIONS 33-35.
