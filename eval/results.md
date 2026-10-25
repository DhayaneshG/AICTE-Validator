# Evaluation results (TEST split)

Generated 2026-10-22T16:34:38+00:00 by `python -m eval.run_eval`. Every figure below was computed by that script; nothing is estimated by hand.

> **Read these numbers as a demonstration, not a validation.** All data is synthetic, generated from a few known templates (seed 42). The TEST split is small (20 applications, 79 documents), so intervals are wide. Thresholds were tuned on the DEV split only (20 applications). Findings are advisory indicators for human review.

## 1. Digital PDFs - extraction and classification

- Classification accuracy: 100.0% (79/79; 95% CI 95%-100%)
- Field accuracy (exact match after whitespace normalisation): 100.0% (385/385; 95% CI 99%-100%)
- Highlighted bounding box re-parses to the correct value: 100.0%
- QR decode: 100.0% (33/33; 95% CI 90%-100%)

| Field | Correct | n | Accuracy |
|---|---|---|---|
| expiry_date | 60 | 60 | 100.0% |
| institution_id | 65 | 65 | 100.0% |
| institution_name | 79 | 79 | 100.0% |
| intake | 44 | 44 | 100.0% |
| issue_date | 79 | 79 | 100.0% |
| letter_id | 25 | 25 | 100.0% |
| reference_id | 33 | 33 | 100.0% |

## 2. Scanned documents (OCR) - three synthetic degradation levels

Scans are generated from the same documents with Augraphy (L1 light, L2 medium, L3 heavy: noise, blur, skew, shading, JPEG). 'Silent wrong' = wrong value that was NOT flagged for manual review (low confidence or missing) - the dangerous case.

| Level | Files | Pages | Field accuracy | CER | Classification | QR decode | Wrong, flagged for review | Wrong, silent | s/page |
|---|---|---|---|---|---|---|---|---|---|
| L1 | 29 | 35 | 83.0% (117/141; 95% CI 76%-88%) | 9.2% | 100.0% | 100.0% (13) | 13 | 11 | 4.81 |
| L2 | 29 | 35 | 60.3% (85/141; 95% CI 52%-68%) | 14.8% | 100.0% | 100.0% (13) | 49 | 7 | 4.93 |
| L3 | 29 | 35 | 49.6% (70/141; 95% CI 42%-58%) | 16.7% | 100.0% | 100.0% (13) | 60 | 11 | 4.71 |

## 3. Per-defect precision / recall (application level, digital PDFs)

| Defect | TEST P | TEST R | TP/FP/FN | (DEV P / R, tuned on) |
|---|---|---|---|---|
| duplicate_file | 1.00 | 1.00 | 2/0/0 | 1.00 / 1.00 |
| expired_accreditation | 1.00 | 1.00 | 2/0/0 | 1.00 / 1.00 |
| expired_lease | 1.00 | 1.00 | 2/0/0 | 1.00 / 1.00 |
| intake_mismatch | 1.00 | 1.00 | 2/0/0 | 1.00 / 1.00 |
| metadata_inconsistent | 1.00 | 1.00 | 2/0/0 | 1.00 / 1.00 |
| missing_document | 1.00 | 1.00 | 2/0/0 | 1.00 / 1.00 |
| name_mismatch | 1.00 | 0.50 | 1/0/1 | 1.00 / 1.00 |
| qr_absent | 1.00 | 1.00 | 2/0/0 | 1.00 / 1.00 |
| qr_wrong_institution | 1.00 | 1.00 | 2/0/0 | 1.00 / 1.00 |

Clean-application false positives on TEST: none. Other medium/high findings on TEST that map to no injected defect: none.

Defect instances in TEST: {'metadata_inconsistent': 2, 'intake_mismatch': 2, 'name_mismatch': 2, 'missing_document': 2, 'expired_lease': 2, 'qr_absent': 2, 'expired_accreditation': 2, 'duplicate_file': 2, 'qr_wrong_institution': 2} (clean apps: 10). With ~2-2 instances per type, a perfect score has a wide interval.

### Name-similarity threshold sensitivity (`name_mismatch`, TEST)

Configured value (tuned on DEV): **75.0**.

| Threshold | Precision | Recall | F1 | TP/FP/FN | False positives on clean apps |
|---|---|---|---|---|---|
| 60 | n/a | 0.00 | n/a | 0/0/2 | 0 |
| 70 | 1.00 | 0.50 | 0.67 | 1/0/1 | 0 |
| 76 | 1.00 | 0.50 | 0.67 | 1/0/1 | 0 |
| 80 | 1.00 | 0.50 | 0.67 | 1/0/1 | 0 |
| 84 | 1.00 | 1.00 | 1.00 | 2/0/0 | 0 |
| 88 | 1.00 | 1.00 | 1.00 | 2/0/0 | 0 |
| 92 | 1.00 | 1.00 | 1.00 | 2/0/0 | 0 |
| 96 | 1.00 | 1.00 | 1.00 | 2/0/0 | 0 |
| 100 | 1.00 | 1.00 | 1.00 | 2/0/0 | 0 |

The configured value was fixed on DEV and is **not** changed after looking at TEST; this table is shown for transparency about how sensitive the result is. Where a higher threshold would have scored better on TEST, that is a real (if small-sample) sign that the DEV-tuned value does not transfer perfectly.


## 4. Signature validation (pyHanko, MOCK trust root)

- TEST split: 6/6 statuses as expected. All 12 signed fixtures: 12/12 (nothing was tuned; validation is deterministic).
  - `sig_01_valid.pdf` (valid): expected valid, got valid
  - `sig_02_valid.pdf` (valid): expected valid, got valid
  - `sig_03_valid.pdf` (valid): expected valid, got valid
  - `sig_04_valid.pdf` (valid): expected valid, got valid
  - `sig_05_valid.pdf` (valid): expected valid, got valid
  - `sig_06_valid.pdf` (valid): expected valid, got valid
  - `sig_07_valid.pdf` (valid): expected valid, got valid
  - `sig_08_valid.pdf` (valid): expected valid, got valid
  - `sig_09_tampered_after_signing.pdf` (tampered_after_signing): expected modified_after_signing, got modified_after_signing
  - `sig_10_untrusted_signer.pdf` (untrusted_signer): expected untrusted_signer, got untrusted_signer
  - `sig_11_expired_certificate.pdf` (expired_certificate): expected expired_certificate, got expired_certificate
  - `sig_12_signer_not_authorised.pdf` (signer_not_authorised): expected signer_not_authorised, got signer_not_authorised

## 5. Tamper indicators on scanned pages (heuristic, advisory)

- Any-indicator recall on tampered scans: 79.2% (19/24; 95% CI 60%-91%)
- Precision: 1.00
- **Clean-scan false-positive rate: 0.0% (0/8; 95% CI 0%-32%)**

| Class | n | Flagged | Localised on the edited region | Flagged at medium |
|---|---|---|---|---|
| clean | 8 | 0 | - | 0 |
| copy_move | 8 | 4 | 3 | 1 |
| retype | 8 | 8 | 6 | 7 |
| erase | 8 | 7 | 7 | 7 |

Detector hits by class: {'copy_move': {'retype': 2, 'copy_move': 1}, 'noise': {'erase': 7, 'retype': 6}, 'ela': {'copy_move': 3, 'erase': 4, 'retype': 5}}. Severity is capped at *medium* in code: these are indicators, not proof. Applies only to image-only pages.

## 6. Verification coverage (MOCK issuer registry)

Documents sent for verification on TEST: 33. Outcomes: {'verified': 28, 'mismatch': 4, 'pending': 1}.
Reasons for non-verified outcomes: {'mismatch:not_found': 2, 'mismatch:institution_mismatch': 2, 'pending:issuer_confirmation_pending': 1}.

Under a simulated TOTAL outage of the MOCK issuer (3 attempts with exponential backoff each): {'source_unavailable': 33} - the pipeline degrades to `source_unavailable` (routed to manual review), never to a silent pass.

## 7. Evidence on findings

Of 20 open/manual-review findings on TEST (digital pipeline): evidence text on 100.0% (20/20; 95% CI 84%-100%); page + bounding box (highlightable) on 80.0% (16/20; 95% CI 58%-92%). Findings without a location are by nature document-level (e.g. a missing document, a duplicate-file hash): {'R-MISS': 2, 'R-DUP': 2}.

## 8. Processing time

- Digital PDFs, extraction + checks (single process, CPU): **2.010 s/application**, 0.509 s/document (20 apps, 79 docs).
- Scans L1 (OCR + preprocessing): 4.81 s/page.
- Scans L2 (OCR + preprocessing): 4.93 s/page.
- Scans L3 (OCR + preprocessing): 4.71 s/page.

Not load-tested; single-process timings on the build machine.

## 9. Alerts, escalation and timeliness (SIMULATED clock)

Simulated 9 days in 5-minute scheduler ticks (2593 ticks, 18 alerts, 12 applications). Time is simulated, so detection lag is bounded by the tick by construction; the point is that the engine meets that bound, never duplicates and respects acknowledgements.

- Detection lag (alert created - alert due): mean 0.22 min, max 4.0 min; 18/18 within one tick.
- Escalation delay: mean 0.27 min, max 4.0 min; 30/30 within one tick.
- Duplicate alerts: 0. Escalations after acknowledgement: 0. Audit chain intact afterwards: True.
- Notifications written: {'email_mock': 49, 'in_app': 49} (the e-mail channel is MOCK - nothing is sent).
- Engine compute: mean 10.9 ms/tick (real time).

| Rule | Alerts | Resolved | Escalations | Max detection lag (min) | Max escalation delay (min) |
|---|---|---|---|---|---|
| AL-CLARIF-OVERDUE | 1 | 0 | 0 | 0.0 | - |
| AL-HIGH-OPEN | 11 | 1 | 18 | 0.0 | 0.0 |
| AL-MEDIUM-OPEN | 5 | 0 | 10 | 0.0 | 0.0 |
| AL-VERIF-STUCK | 1 | 0 | 2 | 4.0 | 4.0 |

## 10. Access control

RBAC / row-isolation matrix (real HTTP requests against a throw-away database): **55/55 cases pass (100.0%)**. Covers unauthenticated and forged tokens, wrong-role (403), other-institution and other-region objects (404), list scoping, and document views.

## 11. Capability audit

From FEATURES.md: 38 capabilities - built 26, mocked 4, planned 7, downgraded 1, partial 0, not started 0.

## 12. Limitations (read before quoting any number)

- **Synthetic data only.** Documents come from a handful of templates with injected defects; real AICTE submissions vary far more. Perfect digital scores here say the rules work as written, not that they would on real files.
- **Small test set.** 20 test applications; see the confidence intervals. Do not extrapolate.
- **Scanned documents:** field accuracy falls with degradation (L1 83.0%, L2 60.3%, L3 49.6%); wrong values that were NOT flagged for review: L1 11, L2 7, L3 11. Pale labels are the main loss. OCR was tuned on DEV scans only.
- **Tamper detection is heuristic and limited:** recall 79.2%, clean-scan false-positive rate 0.0%. It is marked *downgraded* in FEATURES.md, capped at medium severity, and only runs on image-only pages. It cannot prove tampering.
- **Signatures:** validated against a synthetic (MOCK) trust root with synthetic keys; real PKI chains, revocation (OCSP/CRL), timestamps and Indian CCA roots are not exercised.
- **MOCK sources:** AICTE records, issuer verification API, scrutiny history, signature trust root and the e-mail channel are simulated. Real integrations will have different latency, schemas and failure modes.
- **Alert timeliness is simulated** (see section 9): it demonstrates logic, not production latency.
- **Security is demo-grade:** HS256 JWT without refresh/revocation or MFA; in-memory login lockout; encryption key from an environment variable/dev file instead of a KMS; SQLite; single process; no rate limiting beyond login; no penetration test.
- **No load or concurrency testing.** Background jobs run in-process (APScheduler); production would need a queue and workers.
- **Advisory only.** Every finding is an indicator for human review; none is proof of forgery or misconduct. The UI carries a persistent banner saying so.
