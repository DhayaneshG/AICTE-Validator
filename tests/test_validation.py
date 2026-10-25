"""M3 tests: verification statuses/retry, rule engine behaviours, anomaly checks, DEV defect metrics."""
import datetime as dt
import json

import pytest

from backend.mock.issuer_api import MockIssuerAPI
from backend.pipeline import rules as R
from backend.pipeline.orchestrator import CONFIG, load_checklist, load_thresholds
from backend.pipeline.types import FieldValue
from backend.pipeline.verify import verify_reference

SUB = dt.date(2026, 3, 1)
INST = "1-12345678901"
REC = {"institution_id": INST, "name": "Sri Demo Institute of Technology", "approved_intake": 180, "city": "Chennai", "state": "Tamil Nadu",
       "last_eoa_letter_id": f"SRO/{INST}/2025/EOA"}


def fv(name, value, conf=0.97, label="Label", raw=None, page=1, bbox=(10, 10, 100, 20)):
    return FieldValue(name=name, value=value, confidence=conf, page=page, bbox=list(bbox), method="label_right_cell",
                      raw=raw if raw is not None else str(value), label=label)


def doc(doc_type, did=None, sha=None, meta=None, **fields):
    return R.DocCtx(document_id=did or doc_type, filename=f"{doc_type}.pdf", doc_type=doc_type, classification={}, pages=[],
                    fields={k: (v if isinstance(v, FieldValue) else fv(k, v)) for k, v in fields.items()},
                    metadata=meta or {}, sha256=sha or f"sha-{doc_type}")


def ctx_for(docs, category="eoa_renewal_basic", seen=None, thresholds=None, records=REC, checklist=None):
    return R.AppCtx(application_id="APP-T", category=category, institution_id=INST, submission_date=SUB, docs=docs,
                    checklist=checklist or load_checklist(category), thresholds=thresholds or load_thresholds(), records=records,
                    seen_hashes=seen or {})


def by_rule(findings, rid):
    return [f for f in findings if f.rule_id == rid]


def good_docs():
    return [
        doc("affidavit", institution_name="Sri Demo Institute of Technology", institution_id=INST, issue_date="2026-02-20", intake=180),
        doc("noc", institution_name="Sri Demo Inst. of Tech.", institution_id=INST, issue_date="2025-05-01", expiry_date="2028-05-01"),
        doc("lease", institution_name="SRI DEMO INSTITUTE OF TECHNOLOGY", issue_date="2024-01-01", expiry_date="2035-01-01"),
        doc("affiliation_letter", institution_name="Sri Demo Institute of Technology, Chennai", institution_id=INST, issue_date="2025-06-01",
            expiry_date="2028-06-01", intake=180),
    ]


# ------------------------------------------------------------- verification
REG = [
    {"ref_id": "NOC-OK", "doc_type": "noc", "institution_id": INST, "institution_name": "x", "valid_until": "2028-01-01", "status": "active"},
    {"ref_id": "NOC-OTHER", "doc_type": "noc", "institution_id": "1-99999999999", "institution_name": "y", "valid_until": "2028-01-01", "status": "active"},
    {"ref_id": "NOC-EXP", "doc_type": "noc", "institution_id": INST, "institution_name": "x", "valid_until": "2025-01-01", "status": "active"},
    {"ref_id": "NOC-PEND", "doc_type": "noc", "institution_id": INST, "institution_name": "x", "valid_until": "2028-01-01", "status": "pending"},
    {"ref_id": "NOC-FLAKY", "doc_type": "noc", "institution_id": INST, "institution_name": "x", "valid_until": "2028-01-01", "status": "active", "simulate_outage_attempts": 2},
    {"ref_id": "AFL-1", "doc_type": "affiliation_letter", "institution_id": INST, "institution_name": "x", "valid_until": "2028-01-01", "status": "active"},
]


def v(ref, doc_type="noc", api=None, **kw):
    return verify_reference(api or MockIssuerAPI(entries=REG), ref, INST, doc_type, SUB, sleep=lambda s: None, **kw)


def test_all_six_verification_statuses_reachable():
    got = {
        "verified": v("NOC-OK"),
        "mismatch": v("NOC-OTHER"),
        "expired": v("NOC-EXP"),
        "pending": v("NOC-PEND"),
        "source_unavailable": v("NOC-OK", api=MockIssuerAPI(entries=REG, outage_all=True)),
        "manual_review": v(None),
    }
    for k, r in got.items():
        assert r.status == k, (k, r)
        assert r.source == "MOCK issuer registry" and r.timestamp
    assert v("NOC-ABSENT").reason == "not_found" and v("NOC-OTHER").reason == "institution_mismatch"
    assert v("AFL-1", doc_type="noc").reason == "doc_type_mismatch"
    assert v("NOC-OK").raw_response["record"]["ref_id"] == "NOC-OK"  # raw response stored


def test_retry_with_exponential_backoff():
    waits = []
    r = verify_reference(MockIssuerAPI(entries=REG), "NOC-FLAKY", INST, "noc", SUB, max_attempts=3, backoff_base=0.5, sleep=waits.append)
    assert r.status == "verified" and len(r.attempts) == 3
    assert waits == [0.5, 1.0]
    assert [a["outcome"] for a in r.attempts] == ["source_unavailable", "source_unavailable", "response"]
    r2 = verify_reference(MockIssuerAPI(entries=REG), "NOC-FLAKY", INST, "noc", SUB, max_attempts=2, sleep=lambda s: None)
    assert r2.status == "source_unavailable" and len(r2.attempts) == 2


# ------------------------------------------------------------- rules
def test_clean_application_has_no_hard_findings():
    c = ctx_for(good_docs())
    assert [f for f in R.run_checklist(c) if f.status == "open"] == []


def test_missing_document():
    c = ctx_for([d for d in good_docs() if d.doc_type != "lease"])
    f = by_rule(R.run_checklist(c), "R-MISS-LEASE")
    assert len(f) == 1 and f[0].severity == "high" and f[0].status == "open" and "lease" in f[0].evidence_text


def test_missing_with_unclassified_file_routes_to_manual_review():
    docs = [d for d in good_docs() if d.doc_type != "lease"] + [doc("unknown", did="mystery")]
    f = by_rule(R.run_checklist(ctx_for(docs)), "R-MISS-LEASE")
    assert f[0].status == "manual_review" and f[0].severity == "low"


def test_expired_document_and_evidence():
    docs = good_docs()
    docs[2] = doc("lease", institution_name="Sri Demo Institute of Technology", issue_date="2015-01-01", expiry_date="2026-02-01",
                  **{})
    f = by_rule(R.run_checklist(ctx_for(docs)), "R-EXP-LEASE")
    assert len(f) == 1 and f[0].status == "open" and f[0].bbox and f[0].page == 1
    assert "2026-02-01" in f[0].evidence_text and "2026-02-01" in f[0].message


def test_low_confidence_field_never_hard_flags():
    docs = good_docs()
    docs[2] = doc("lease", institution_name="Sri Demo Institute of Technology", issue_date="2015-01-01",
                  expiry_date=fv("expiry_date", "2020-01-01", conf=0.3))  # looks expired but unreliable
    f = R.run_checklist(ctx_for(docs))
    exp = by_rule(f, "R-EXP-LEASE")
    assert len(exp) == 1 and exp[0].status == "manual_review" and exp[0].severity == "low"
    assert not [x for x in f if x.status == "open"]


def test_missing_expiry_field_routes_to_manual_review():
    docs = good_docs()
    docs[1] = doc("noc", institution_name="Sri Demo Inst. of Tech.", institution_id=INST, issue_date="2025-05-01")
    exp = by_rule(R.run_checklist(ctx_for(docs)), "R-EXP-NOC")
    assert exp and exp[0].status == "manual_review"


def test_name_mismatch_flags_only_the_odd_doc_and_benign_variants_pass():
    docs = good_docs()
    docs[3] = doc("affiliation_letter", institution_name="Meridian College of Arts and Science", institution_id=INST, issue_date="2025-06-01",
                  expiry_date="2028-06-01", intake=180)
    f = R.run_checklist(ctx_for(docs))
    n = by_rule(f, "R-NAME-CONSIST")
    assert [x.details["doc_type"] for x in n] == ["affiliation_letter"]
    assert by_rule(f, "R-REC-NAME") and by_rule(f, "R-REC-NAME")[0].details["doc_type"] == "affiliation_letter"


def test_cross_document_identifier_mismatch():
    docs = good_docs()
    docs[0] = doc("affidavit", institution_name="Sri Demo Institute of Technology", institution_id="1-55555555555", issue_date="2026-02-20", intake=180)
    f = by_rule(R.run_checklist(ctx_for(docs)), "R-ID-CONSIST")
    assert len(f) == 1 and f[0].details["found"] == "1-55555555555" and f[0].details["doc_type"] == "affidavit"
    docs = good_docs()
    docs[0] = doc("affidavit", institution_name="Sri Demo Institute of Technology", institution_id=INST, issue_date="2026-02-20", intake=180,
                  letter_id="SRO/1-55555555555/2025/EOA")
    assert by_rule(R.run_checklist(ctx_for(docs)), "R-ID-CONSIST")


def test_intake_cross_check_with_mock_records():
    docs = good_docs()
    docs[0] = doc("affidavit", institution_name="Sri Demo Institute of Technology", institution_id=INST, issue_date="2026-02-20", intake=240)
    f = by_rule(R.run_checklist(ctx_for(docs)), "R-REC-INTAKE")
    assert len(f) == 1 and f[0].details["records_intake"] == 180 and f[0].details["document_intake"] == 240


def test_unknown_institution_in_records_routes_to_manual_review():
    f = R.run_checklist(ctx_for(good_docs(), records=None))
    rn = by_rule(f, "R-REC-NAME")
    assert rn and rn[0].status == "manual_review"


def test_metadata_anomaly_thresholds():
    t = load_thresholds()
    docs = good_docs()
    docs[0].metadata = {"creation_date": "2026-02-21"}  # +1 day: fine
    docs[1].metadata = {"creation_date": "2025-04-01"}  # 30 days BEFORE stated issue date: inconsistent
    docs[2].metadata = {"creation_date": "2027-06-01"}  # long after: inconsistent
    f = by_rule(R.run_checklist(ctx_for(docs)), "R-META")
    assert sorted(x.details["doc_type"] for x in f) == ["lease", "noc"]
    assert all(x.source == "anomaly" and "PDF CreationDate" in x.evidence_text for x in f)
    assert t["metadata_days_before_max"] >= 0


def test_duplicate_detection_cross_and_within_application():
    docs = good_docs()
    seen = {docs[2].sha256: ("APP-EARLIER", "APP-EARLIER:upload_2.pdf")}
    f = by_rule(R.run_checklist(ctx_for(docs, seen=seen)), "R-DUP")
    assert len(f) == 1 and f[0].details["duplicate_of_application"] == "APP-EARLIER" and f[0].source == "anomaly"
    # first submission is NOT flagged against itself
    assert not by_rule(R.run_checklist(ctx_for(good_docs(), seen={})), "R-DUP")
    docs = good_docs()
    docs[3].sha256 = docs[0].sha256
    assert len(by_rule(R.run_checklist(ctx_for(docs)), "R-DUP")) == 1  # same file twice in one application


def test_issuer_verification_findings_map_statuses():
    docs = good_docs()
    c = ctx_for(docs)
    c.verifications = {"noc": v("NOC-ABSENT"), "affiliation_letter": v("NOC-OTHER", doc_type="affiliation_letter")}
    f = by_rule(R.run_checklist(c), "R-VER-NOC")
    assert f[0].details["reason"] == "not_found" and f[0].severity == "high" and f[0].source == "verification"
    c.verifications = {"noc": v("NOC-PEND")}
    f = by_rule(R.run_checklist(c), "R-VER-NOC")
    assert f[0].severity == "low" and f[0].status == "open"
    c.verifications = {"noc": v("NOC-OK", api=MockIssuerAPI(entries=REG, outage_all=True))}
    f = by_rule(R.run_checklist(c), "R-VER-NOC")
    assert f[0].status == "manual_review"


def test_checklists_are_data_driven():
    cl = load_checklist("eoa_renewal_basic")
    cl = json.loads(json.dumps(cl))
    cl["rules"] = [r for r in cl["rules"] if r["id"] != "R-EXP-LEASE"]
    for r in cl["rules"]:
        if r["id"] == "R-MISS-LEASE":
            r["severity"] = "low"
    docs = [d for d in good_docs() if d.doc_type != "lease"]
    f = R.run_checklist(ctx_for(docs, checklist=cl))
    assert by_rule(f, "R-MISS-LEASE")[0].severity == "low"
    docs.append(doc("lease", institution_name="Sri Demo Institute of Technology", issue_date="2010-01-01", expiry_date="2011-01-01"))
    assert not by_rule(R.run_checklist(ctx_for(docs, checklist=cl)), "R-EXP-LEASE")  # rule removed from JSON -> not run


def test_finding_schema_fields():
    c = ctx_for([d for d in good_docs() if d.doc_type != "noc"])
    f = R.run_checklist(c)[0]
    for k in ["id", "application_id", "document_id", "rule_id", "severity", "source", "message", "evidence_text", "page", "bbox",
              "confidence", "status", "created_at"]:
        assert hasattr(f, k)
    assert f.status in {"open", "accepted", "overridden", "clarification_requested", "manual_review"}


# ------------------------------------------------------------- end-to-end on DEV
@pytest.fixture(scope="module")
def dev_run():
    from eval.defect_eval import run
    return run("dev")


def test_thresholds_tuned_on_dev_only_provenance():
    t = json.loads((CONFIG / "thresholds.json").read_text())
    assert t["provenance"]["tuned_on"] == "dev split only"


def test_dev_defect_metrics(dev_run):
    s = dev_run["score"]["per_defect_app"]
    assert len(s) == 9
    for t, m in s.items():
        assert m["recall"] >= 0.5 and m["precision"] >= 0.5, (t, m)
    assert sum(m["tp"] for m in s.values()) >= 15


def test_every_finding_has_evidence_text(dev_run):
    allf = [f for r in dev_run["results"].values() for f in r["findings"]]
    assert allf and all(f.evidence_text for f in allf)


# ---------------------------------------------------------------- APH opt-in rules
def _with_rules(category="eoa_renewal_basic"):
    chk = json.loads(json.dumps(load_checklist(category)))
    for r in chk["rules"]:
        if r["id"].startswith("R-APH"):
            r["enabled"] = True
    return chk


def test_aph_rules_ship_disabled_and_do_not_fire():
    docs = good_docs()
    assert [f for f in R.run_checklist(ctx_for(docs)) if f.rule_id.startswith("R-APH")] == []


def test_aph_lease_term_remaining_flags_short_lease_only():
    chk = _with_rules()
    short = good_docs()
    for d in short:
        if d.doc_type == "lease":
            d.fields["expiry_date"] = fv("expiry_date", "2028-06-30")  # ~2.3 years after SUB
    f = by_rule(R.run_checklist(ctx_for(short, checklist=chk)), "R-APH-LEASE-TERM")
    assert len(f) == 1 and f[0].details["years_left"] < 4 and f[0].details["aph"] == "2.1(e)"
    long = good_docs()
    for d in long:
        if d.doc_type == "lease":
            d.fields["expiry_date"] = fv("expiry_date", "2040-01-01")
    assert by_rule(R.run_checklist(ctx_for(long, checklist=chk)), "R-APH-LEASE-TERM") == []
    expired = good_docs()
    for d in expired:
        if d.doc_type == "lease":
            d.fields["expiry_date"] = fv("expiry_date", "2025-01-01")
    assert by_rule(R.run_checklist(ctx_for(expired, checklist=chk)), "R-APH-LEASE-TERM") == []  # R-EXP-LEASE owns expired leases


def test_aph_dsc_required_flags_unsigned_documents():
    from backend.pipeline import authenticity
    authenticity.R.CHECKS["dsc_present"] = authenticity.check_dsc_present
    chk = _with_rules()
    docs = good_docs()
    docs[0].signature = {"status": "valid"}
    f = by_rule(R.run_checklist(ctx_for(docs, checklist=chk)), "R-APH-DSC")
    assert len(f) == len(docs) - 1 and all(x.source == "signature" for x in f)
