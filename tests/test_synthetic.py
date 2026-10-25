"""M1 tests: synthetic corpus is complete, labelled consistently and reproducible."""
import datetime as dt
import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "data/synthetic"
MOCK = ROOT / "backend/mock"


@pytest.fixture(scope="module")
def labels():
    return json.loads((SYN / "labels.json").read_text())


@pytest.fixture(scope="module")
def records():
    return {r["institution_id"]: r for r in json.loads((MOCK / "records.json").read_text())["institutions"]}


@pytest.fixture(scope="module")
def registry():
    return {e["ref_id"]: e for e in json.loads((MOCK / "issuer_registry.json").read_text())["entries"]}


def test_checklists_are_data_driven_and_well_formed():
    for name, docs in {
        "eoa_renewal_basic": {"affidavit", "noc", "lease", "affiliation_letter"},
        "eoa_new_course": {"affidavit", "noc", "lease", "affiliation_letter", "accreditation_certificate"},
        "eoa_intake_change": {"affidavit", "affiliation_letter", "previous_eoa_letter"},
    }.items():
        c = json.loads((ROOT / "backend/config/checklists" / f"{name}.json").read_text())
        assert set(c["required_documents"]) == docs
        for r in c["rules"]:
            assert {"id", "doc", "check", "severity", "message"} <= set(r)
            assert r["severity"] in {"low", "medium", "high"}
        ids = [r["id"] for r in c["rules"]]
        assert len(ids) == len(set(ids))


def test_dataset_shape(labels):
    apps = labels["applications"]
    assert len(apps) == 40
    assert Counter(a["label"] for a in apps) == {"clean": 20, "defective": 20}
    assert Counter(a["split"] for a in apps) == {"dev": 20, "test": 20}
    for sp in ("dev", "test"):
        assert Counter(a["label"] for a in apps if a["split"] == sp) == {"clean": 10, "defective": 10}


def test_each_defect_at_least_three_times_in_both_splits(labels):
    c = Counter((d["defect_type"], a["split"]) for a in labels["applications"] for d in a["defects"])
    total = Counter(d["defect_type"] for a in labels["applications"] for d in a["defects"])
    assert set(total) == set(labels["defect_types"]) and len(total) == 9
    for t, n in total.items():
        assert n >= 3, t
        assert c[(t, "dev")] >= 1 and c[(t, "test")] >= 1, t


def test_clean_apps_have_no_defects_and_all_required_docs(labels):
    for a in labels["applications"]:
        if a["label"] == "clean":
            assert a["defects"] == []
            assert {d["doc_type"] for d in a["documents"]} == set(a["required_documents"])


def test_missing_document_label_matches_files(labels):
    for a in labels["applications"]:
        miss = [d["doc_type"] for d in a["defects"] if d["defect_type"] == "missing_document"]
        present = {d["doc_type"] for d in a["documents"]}
        for m in miss:
            assert m not in present and m in a["required_documents"]
        assert set(a["required_documents"]) - present == set(miss)


def test_files_exist_hashes_match_and_text_layer(labels):
    import pymupdf
    for a in labels["applications"]:
        for d in a["documents"]:
            f = SYN / "applications" / a["application_id"] / d["filename"]
            data = f.read_bytes()
            assert hashlib.sha256(data).hexdigest() == d["sha256"]
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                assert len(doc[0].get_text().strip()) > 50  # text layer present


def test_duplicates_are_exactly_the_labelled_ones(labels):
    h = Counter(d["sha256"] for a in labels["applications"] for d in a["documents"])
    dup_hashes = {k for k, v in h.items() if v > 1}
    labelled = {d["sha256"] for a in labels["applications"] for d in a["documents"] if d.get("reused_from")}
    assert dup_hashes == labelled and len(dup_hashes) == 4
    for a in labels["applications"]:
        for d in a["documents"]:
            if d.get("reused_from"):
                assert any(x["defect_type"] == "duplicate_file" and x["doc_type"] == d["doc_type"] for x in a["defects"])


def test_expiry_labels_are_true(labels):
    for a in labels["applications"]:
        sub = dt.date.fromisoformat(a["submission_date"])
        defect_docs = {(x["defect_type"], x["doc_type"]) for x in a["defects"]}
        for d in a["documents"]:
            exp = d["fields"]["expiry_date"]
            if not exp or d.get("reused_from"):
                continue
            expired = dt.date.fromisoformat(exp) < sub
            if d["doc_type"] == "lease":
                assert expired == (("expired_lease", "lease") in defect_docs)
            elif d["doc_type"] == "accreditation_certificate":
                assert expired == (("expired_accreditation", "accreditation_certificate") in defect_docs)
            else:
                assert not expired, (a["application_id"], d["doc_type"])


def test_intake_labels_true(labels, records):
    for a in labels["applications"]:
        rec = records[a["institution_id"]]["approved_intake"]
        defect_docs = {(x["defect_type"], x["doc_type"]) for x in a["defects"]}
        for d in a["documents"]:
            it = d["fields"]["intake"]
            if it is None or d.get("reused_from"):
                continue
            assert (it != rec) == (("intake_mismatch", d["doc_type"]) in defect_docs)


def test_name_labels_true(labels):
    for a in labels["applications"]:
        defect_docs = {x["doc_type"] for x in a["defects"] if x["defect_type"] == "name_mismatch"}
        for d in a["documents"]:
            if d.get("reused_from"):
                continue
            kind = d["name_variant"]
            assert kind.startswith("mismatch") == (d["doc_type"] in defect_docs)


def test_qr_registry_consistency(labels, registry):
    for a in labels["applications"]:
        qa = {x["doc_type"] for x in a["defects"] if x["defect_type"] == "qr_absent"}
        qw = {x["doc_type"] for x in a["defects"] if x["defect_type"] == "qr_wrong_institution"}
        for d in a["documents"]:
            ref = d["fields"]["reference_id"]
            if d["doc_type"] not in ("noc", "affiliation_letter"):
                assert ref is None
                continue
            assert ref
            if d["doc_type"] in qa and not d.get("reused_from"):
                assert ref not in registry
            elif d["doc_type"] in qw and not d.get("reused_from"):
                assert registry[ref]["institution_id"] != a["institution_id"]
            else:
                assert registry[ref]["institution_id"] == a["institution_id"], (a["application_id"], d["doc_type"])


def test_metadata_offsets_separable(labels):
    clean, bad = [], []
    for a in labels["applications"]:
        mi = {x["doc_type"] for x in a["defects"] if x["defect_type"] == "metadata_inconsistent"}
        for d in a["documents"]:
            if d.get("reused_from"):
                continue
            off = (dt.date.fromisoformat(d["creation_date"]) - dt.date.fromisoformat(d["fields"]["issue_date"])).days
            (bad if d["doc_type"] in mi else clean).append(off)
    assert min(clean) >= 0 and max(clean) <= 31
    assert all(o < -15 or o > 35 for o in bad) and len(bad) >= 3


def test_letter_ids_format(labels):
    import re
    pat = re.compile(r"^(SRO|SWRO|WRO|NRO|ERO|CRO)/1-\d{11}/20\d\d/EOA$")
    n = 0
    for a in labels["applications"]:
        for d in a["documents"]:
            lid = d["fields"]["letter_id"]
            if lid:
                n += 1
                assert pat.match(lid), lid
    assert n > 10


def test_reproducible_with_fixed_seed():
    from data.generator.build import build
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        r = build(t / "s", t / "m")
    on_disk = json.loads((SYN / "manifest.json").read_text())
    assert r["manifest"] == on_disk
