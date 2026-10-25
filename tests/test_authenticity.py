"""M7: signature validation (MOCK trust root) and tamper indicators, wired through the rule engine."""
import json
from pathlib import Path

import pytest

from backend.pipeline import authenticity, signatures, tamper
from backend.pipeline.orchestrator import Pipeline
from backend.pipeline.ocr_router import route

SYN = Path(__file__).resolve().parents[1] / "data" / "synthetic"
SIGNED = json.loads((SYN / "signed" / "labels.json").read_text())["items"]
TAMPER = json.loads((SYN / "tamper" / "labels.json").read_text())["items"]


def test_trust_root_is_mock_and_policy_loaded():
    pol = signatures.load_policy()
    assert (Path(__file__).resolve().parents[1] / pol["trust_roots"][0]).exists()
    assert "MOCK" in signatures.TRUST_NOTE


@pytest.mark.parametrize("it", SIGNED, ids=[i["file"] for i in SIGNED])
def test_signature_outcomes(it):
    r = signatures.validate_pdf_signatures((SYN / "signed" / it["file"]).read_bytes())
    assert r["status"] == it["expected_status"], r
    assert r["trust_root"] == "MOCK"


def test_four_negatives_exist_and_unsigned_is_not_an_error():
    assert sum(1 for i in SIGNED if i["kind"] != "valid") == 4 and sum(1 for i in SIGNED if i["kind"] == "valid") == 8
    plain = (SYN / "applications" / "APP-0001" / "upload_1.pdf").read_bytes()
    assert signatures.validate_pdf_signatures(plain)["status"] == "no_signature"
    assert signatures.validate_pdf_signatures(b"not a pdf")["status"] == "error"


def test_digital_pages_are_not_image_analysed():
    assert tamper.analyze_pdf((SYN / "applications" / "APP-0001" / "upload_1.pdf").read_bytes()) == []


def _pipe():
    p = Pipeline(ocr_router=route)
    authenticity.register(p)
    return p


def _app(pipe, files):
    docs = [pipe.analyze_file(str(i), fn, data) for i, (fn, data) in enumerate(files, 1)]
    app = {"application_id": "APP-T", "category": "eoa_renewal_basic", "institution_id": "1-00000000000", "submission_date": "2026-03-01"}
    findings, _ = pipe.check_application(app, docs, {})
    return docs, findings


def test_signature_rules_raise_findings_only_for_bad_signatures():
    pipe = _pipe()
    bad = next(i for i in SIGNED if i["kind"] == "tampered_after_signing")
    good = next(i for i in SIGNED if i["kind"] == "valid")
    docs, f = _app(pipe, [(bad["file"], (SYN / "signed" / bad["file"]).read_bytes()), (good["file"], (SYN / "signed" / good["file"]).read_bytes())])
    sig = [x for x in f if x.rule_id == "R-SIG"]
    assert len(sig) == 1 and sig[0].severity == "high" and sig[0].source == "signature" and sig[0].document_id == "1"
    assert docs[1].signature["status"] == "valid"


def test_tamper_indicator_becomes_capped_finding_with_bbox():
    pipe = _pipe()
    it = next(i for i in TAMPER if i["split"] == "dev" and i["kind"] == "erase")
    regs = tamper.analyze_pdf((SYN / "tamper" / it["file"]).read_bytes(), pipe.thresholds["tamper"])
    if not any(r["type"] == "noise" for r in regs):  # pick the first dev erase sample the noise detector catches
        for it in (i for i in TAMPER if i["split"] == "dev" and i["kind"] == "erase"):
            regs = tamper.analyze_pdf((SYN / "tamper" / it["file"]).read_bytes(), pipe.thresholds["tamper"])
            if any(r["type"] == "noise" for r in regs):
                break
    assert any(r["type"] == "noise" for r in regs)
    docs, f = _app(pipe, [(it["file"], (SYN / "tamper" / it["file"]).read_bytes())])
    t = [x for x in f if x.rule_id == "R-TAMPER"]
    assert t and all(x.severity in ("low", "medium") and x.source == "tamper" and x.bbox and x.page == 1 for x in t)
    assert "Advisory" in t[0].message


def test_severity_never_exceeds_medium_even_if_configured_high():
    pipe = _pipe()
    pipe.thresholds["tamper"] = {**pipe.thresholds["tamper"], "severity": {"noise": "high", "ela": "high", "copy_move": "high", "text_line": "high"}}
    authenticity.register(pipe)
    it = next(i for i in TAMPER if i["split"] == "dev" and i["kind"] == "erase")
    docs, f = _app(pipe, [(it["file"], (SYN / "tamper" / it["file"]).read_bytes())])
    assert all(x.severity != "high" for x in f if x.rule_id == "R-TAMPER")
