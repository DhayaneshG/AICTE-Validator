"""Synthetic corpus builder (seed=42). Run:  python -m data.generator.build

Produces (all MOCK / synthetic, no real data):
  data/synthetic/applications/APP-xxxx/upload_n.pdf   digital PDFs with text layer
  data/synthetic/labels.json                          ground truth, defects, dev/test split
  data/synthetic/manifest.json                        sha256 of every generated file (reproducibility)
  backend/mock/records.json                           MOCK AICTE institution records
  backend/mock/issuer_registry.json                   MOCK issuer registry (QR reference -> institution)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
import shutil
from pathlib import Path

import fitz  # PyMuPDF
import qrcode
from faker import Faker
from rapidfuzz import fuzz

from . import docs as D
from .render import render_pdf

SEED = 42
ROOT = Path(__file__).resolve().parents[2]
UTC = dt.timezone.utc

REGIONS = {
    "SRO": [("Chennai", "Tamil Nadu"), ("Coimbatore", "Tamil Nadu"), ("Madurai", "Tamil Nadu"), ("Kochi", "Kerala")],
    "SWRO": [("Bengaluru", "Karnataka"), ("Mysuru", "Karnataka"), ("Hyderabad", "Telangana"), ("Visakhapatnam", "Andhra Pradesh")],
    "WRO": [("Pune", "Maharashtra"), ("Mumbai", "Maharashtra"), ("Ahmedabad", "Gujarat"), ("Nagpur", "Maharashtra")],
    "NRO": [("Lucknow", "Uttar Pradesh"), ("Chandigarh", "Chandigarh"), ("Jaipur", "Rajasthan"), ("Dehradun", "Uttarakhand")],
    "ERO": [("Kolkata", "West Bengal"), ("Bhubaneswar", "Odisha"), ("Patna", "Bihar"), ("Guwahati", "Assam")],
    "CRO": [("Bhopal", "Madhya Pradesh"), ("Indore", "Madhya Pradesh"), ("Raipur", "Chhattisgarh"), ("Jabalpur", "Madhya Pradesh")],
}
REGION_LIST = list(REGIONS)
PREFIXES = ["Sri", "Shri", "Saint", "Annai", "Bharathi", "Kamaraj", "Vivekananda", "Maharishi", "Rajiv", "Netaji",
            "Lakshmi", "Saraswathi", "Vidya", "Ganga", "Himalaya", "Sunrise", "Pioneer", "Meridian", "Crescent", "Heritage"]
TYPES = ["Institute of Technology", "College of Engineering", "Engineering College", "Institute of Engineering and Technology",
         "School of Engineering and Technology", "Institute of Science and Technology"]
SWAP_TYPES = ["College of Arts and Science", "Institute of Management Studies", "Polytechnic College", "Institute of Medical Sciences"]
COURSES = ["B.E. Computer Science and Engineering", "B.E. Electronics and Communication Engineering", "B.E. Mechanical Engineering",
           "B.E. Artificial Intelligence and Data Science", "B.E. Civil Engineering"]
FONT_KEYS = ["sans", "serif", "mono"]
FONT_SIZES = {"sans": [9.5, 10], "serif": [10.5, 11], "mono": [8.5, 9]}
CATEGORY_SPECIFIC = {"affidavit", "previous_eoa_letter"}

CHECKLISTS = {k: json.loads((ROOT / "backend/config/checklists" / f"{k}.json").read_text())
              for k in ["eoa_renewal_basic", "eoa_new_course", "eoa_intake_change"]}
NC, RN, IC = "eoa_new_course", "eoa_renewal_basic", "eoa_intake_change"

# ---- defect plan: each of the 9 defect types appears exactly 4 times (2 dev + 2 test) -----------
# codes: EL expired_lease, NM name_mismatch, IM intake_mismatch, MD missing_document, DF duplicate_file,
#        MI metadata_inconsistent, QA qr_absent, QW qr_wrong_institution, EA expired_accreditation
DEFECT_NAMES = {"EL": "expired_lease", "NM": "name_mismatch", "IM": "intake_mismatch", "MD": "missing_document",
                "DF": "duplicate_file", "MI": "metadata_inconsistent", "QA": "qr_absent",
                "QW": "qr_wrong_institution", "EA": "expired_accreditation"}
DEFECTIVE_PLAN = [
    # key, category, defects, split
    ("D01", NC, ["EA", "EL"], "dev"), ("D03", NC, ["EA", "IM"], "dev"), ("D05", NC, ["MI", "MD"], "dev"),
    ("D08", RN, ["EL", "DF"], "dev"), ("D09", RN, ["NM", "QA"], "dev"), ("D11", RN, ["MD"], "dev"),
    ("D14", IC, ["IM", "NM"], "dev"), ("D16", IC, ["QW", "MI"], "dev"), ("D18", IC, ["DF", "QA"], "dev"),
    ("D20", IC, ["QW"], "dev"),
    ("D02", NC, ["EA", "QA"], "test"), ("D04", NC, ["EA", "NM"], "test"), ("D06", NC, ["DF", "QW"], "test"),
    ("D07", RN, ["EL"], "test"), ("D10", RN, ["IM", "MI"], "test"), ("D12", RN, ["QW", "DF"], "test"),
    ("D13", RN, ["EL", "MD"], "test"), ("D15", IC, ["IM", "QA"], "test"), ("D17", IC, ["NM", "MD"], "test"),
    ("D19", IC, ["MI"], "test"),
]
# clean apps: (key, category, split, df_source_for)  -- df_source_for names the defective app that reuses a file of it
CLEAN_PLAN = [
    ("C01", NC, "dev", None), ("C02", NC, "dev", None), ("C03", NC, "dev", None),
    ("C04", NC, "test", "D06"), ("C05", NC, "test", None), ("C06", NC, "test", None), ("C07", NC, "test", None),
    ("C08", RN, "dev", "D08"), ("C09", RN, "dev", None), ("C10", RN, "dev", None), ("C11", RN, "dev", None),
    ("C12", RN, "test", "D12"), ("C13", RN, "test", None), ("C14", RN, "test", None),
    ("C15", IC, "dev", "D18"), ("C16", IC, "dev", None), ("C17", IC, "dev", None),
    ("C18", IC, "test", None), ("C19", IC, "test", None), ("C20", IC, "test", None),
]
DEFECT_ORDER = ["EA", "EL", "QA", "QW", "IM", "DF", "NM", "MI", "MD"]
# clean apps that carry a 'pending' issuer record / simulated issuer outage (non-defect verification states)
PENDING_CLEAN = ["C02", "C13"]
OUTAGE_CLEAN = {"C05": 2, "C16": 1}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def ts(d: dt.date, rng, extra_days: int = 0) -> float:
    base = dt.datetime(d.year, d.month, d.day, rng.randrange(8, 19), rng.randrange(60), tzinfo=UTC)
    return (base + dt.timedelta(days=extra_days)).timestamp()


def pdf_date(t: float) -> str:
    return dt.datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d")


def make_institution(rng, fake, used, idx, forced_region=None):
    for _ in range(200):
        pre = D.pick(rng, PREFIXES)
        sur = fake.last_name()
        typ = D.pick(rng, TYPES)
        name = f"{pre} {sur} {typ}" if rng.random() < 0.75 else f"{sur} {typ}"
        if all(fuzz.token_set_ratio(name.lower(), u.lower()) < 70 for u in used):
            break
    used.append(name)
    region = forced_region or REGION_LIST[idx % len(REGION_LIST)]
    city, state = D.pick(rng, REGIONS[region])
    inst_id = f"1-{rng.randrange(10**10, 10**11)}"
    intake = D.pick(rng, [60, 120, 180, 240, 300, 360])
    prev_issue = dt.date(2025, rng.randrange(3, 7), rng.randrange(1, 28))
    return {
        "institution_id": inst_id, "name": name, "region": region, "city": city, "state": state,
        "approved_intake": intake, "established_year": rng.randrange(1995, 2019),
        "last_eoa_letter_id": f"{region}/{inst_id}/2025/EOA", "last_eoa_issue_date": prev_issue.isoformat(),
        "last_eoa_valid_until": "2026-06-30", "status": "approved",
    }


def build(out_dir: Path | None = None, mock_dir: Path | None = None) -> dict:
    out_dir = Path(out_dir or ROOT / "data/synthetic")
    mock_dir = Path(mock_dir or ROOT / "backend/mock")
    if out_dir.exists():
        shutil.rmtree(out_dir / "applications", ignore_errors=True)
    (out_dir / "applications").mkdir(parents=True, exist_ok=True)
    mock_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)
    fake = Faker("en_IN")
    fake.seed_instance(SEED)

    # ---- institutions
    used_names: list[str] = []
    plan = []  # dicts
    for key, cat, split, src_for in CLEAN_PLAN:
        plan.append(dict(key=key, category=cat, split=split, defects=[], clean=True, df_target=src_for))
    for key, cat, defects, split in DEFECTIVE_PLAN:
        plan.append(dict(key=key, category=cat, split=split, defects=defects, clean=False, df_target=None))
    by_key = {p["key"]: p for p in plan}
    institutions: dict[str, dict] = {}  # keyed by app key (owner) -> inst
    idx = 0
    for p in plan:
        if p["key"].startswith("D") and "DF" in p["defects"]:
            continue  # shares source institution
        institutions[p["key"]] = make_institution(rng, fake, used_names, idx)
        idx += 1
    for p in plan:
        if p["clean"] and p["df_target"]:
            institutions[p["df_target"]] = institutions[p["key"]]
    decoys = [make_institution(rng, fake, used_names, 100 + i) for i in range(10)]
    all_records = {i["institution_id"]: i for i in list(institutions.values()) + decoys}

    # ---- application ordering (random, but DF source precedes its target)
    order = [p["key"] for p in plan]
    rng.shuffle(order)
    for p in plan:
        if p["clean"] and p["df_target"]:
            a, b = order.index(p["key"]), order.index(p["df_target"])
            if a > b:
                order[a], order[b] = order[b], order[a]
    app_ids = {k: f"APP-{i + 1:04d}" for i, k in enumerate(order)}

    registry: dict[str, dict] = {}
    ref_counter = [4100]
    gen: dict[str, dict] = {}  # key -> generated app info (docs w/ bytes) for DF reuse
    labels_apps = []

    for key in order:
        p = by_key[key]
        inst = institutions[key]
        app_id = app_ids[key]
        cat = p["category"]
        required = CHECKLISTS[cat]["required_documents"]
        sub = dt.date(2026, 2, 2) + dt.timedelta(days=rng.randrange(0, 54))
        defects = list(p["defects"])
        ctx = dict(city=inst["city"], state=inst["state"], region=inst["region"], category=cat,
                   signer=(fake.name(), D.pick(rng, ["Secretary", "Chairman", "Principal", "Managing Trustee"])),
                   notary=f"Adv. {fake.name()}", lessor=f"{fake.last_name()} Educational Trust",
                   survey=f"{rng.randrange(10, 900)}/{rng.randrange(1, 9)}{D.pick(rng, 'ABC')}",
                   acres=f"{rng.randrange(8, 40)}.{rng.randrange(0, 100):02d}",
                   university=f"{inst['state']} Technological University",
                   course=D.pick(rng, COURSES))

        # -- choose target docs for each defect (distinct docs)
        used_docs: set[str] = set()
        targets: dict[str, str | None] = {}
        df_src = None
        if "DF" in defects:
            df_src_key = next(c["key"] for c in plan if c["clean"] and c["df_target"] == key)
            df_src = gen[df_src_key]
        for code in DEFECT_ORDER:
            if code not in defects:
                continue
            if code == "EA":
                cands = ["accreditation_certificate"]
            elif code == "EL":
                cands = ["lease"]
            elif code in ("QA", "QW"):
                cands = [d for d in ["noc", "affiliation_letter"] if d in required]
            elif code == "IM":
                cands = [d for d in ["affidavit", "affiliation_letter", "previous_eoa_letter"] if d in required]
            elif code == "DF":
                src_types = {dd["doc_type"] for dd in df_src["docs"]}
                cands = [d for d in required if d in src_types and (d not in CATEGORY_SPECIFIC or df_src["category"] == cat)]
            else:
                cands = list(required)
            cands = [c for c in cands if c not in used_docs]
            assert cands, (key, code, used_docs)
            targets[code] = D.pick(rng, sorted(cands))
            used_docs.add(targets[code])
        missing = targets.get("MD")
        doc_types = [d for d in required if d != missing]

        defect_labels = []
        app_docs = []
        local_seq = 0
        order_docs = list(doc_types)
        rng.shuffle(order_docs)  # upload order is shuffled so file names carry no information
        for doc_type in order_docs:
            local_seq += 1
            fname = f"upload_{local_seq}.pdf"
            doc_defects = [c for c, t in targets.items() if t == doc_type and c != "MD"]
            rec: dict = {"filename": fname, "doc_type": doc_type}

            # ---------- DF: reuse bytes from source application
            if "DF" in doc_defects:
                srcdoc = next(d for d in df_src["docs"] if d["doc_type"] == doc_type)
                data = srcdoc["bytes"]
                (out_dir / "applications" / app_id).mkdir(parents=True, exist_ok=True)
                (out_dir / "applications" / app_id / fname).write_bytes(data)
                rec.update(sha256=sha(data), fields=dict(srcdoc["fields"]), layout=srcdoc["layout"], font=srcdoc["font"],
                           pages=srcdoc["pages"], name_variant=srcdoc["name_variant"], creation_date=srcdoc["creation_date"],
                           reused_from={"application_id": app_ids[df_src["key"]], "filename": srcdoc["filename"]})
                defect_labels.append({"defect_type": "duplicate_file", "doc_type": doc_type,
                                      "detail": {"source_application_id": app_ids[df_src["key"]], "source_filename": srcdoc["filename"]}})
                app_docs.append(dict(rec, bytes=data))
                continue

            # ---------- values
            written, variant = D.benign_variant(inst["name"], inst["city"], rng)
            if "NM" in doc_defects:
                if rng.random() < 0.5:
                    others = [i["name"] for i in decoys + list(institutions.values()) if i["name"] != inst["name"]]
                    written = D.pick(rng, others)
                    variant = "mismatch_other_institution"
                else:
                    base = inst["name"]
                    for t in TYPES:
                        if base.endswith(t):
                            base = base[: -len(t)].strip()
                    written = f"{base} {D.pick(rng, SWAP_TYPES)}"
                    variant = "mismatch_swapped_type"
            v: dict = {"name": written, "inst_id": None if doc_type == "lease" else inst["institution_id"]}
            rec_intake = inst["approved_intake"]
            intake = rec_intake
            if "IM" in doc_defects:
                intake = max(60, rec_intake + D.pick(rng, [60, 120, -60, 180]))
                if intake == rec_intake:
                    intake = rec_intake + 60
            ref = None
            if doc_type == "affidavit":
                v.update(issue=sub - dt.timedelta(days=rng.randrange(1, 26)), intake=intake,
                         letter_id=inst["last_eoa_letter_id"], prev_date=dt.date.fromisoformat(inst["last_eoa_issue_date"]))
                if cat in (NC, IC):
                    v["proposed"] = rec_intake + D.pick(rng, [60, 120]) if cat == IC else D.pick(rng, [60, 120])
            elif doc_type == "noc":
                issue = sub - dt.timedelta(days=rng.randrange(30, 700))
                v.update(issue=issue, expiry=issue + dt.timedelta(days=3 * 365))
            elif doc_type == "lease":
                if "EL" in doc_defects:
                    end = sub - dt.timedelta(days=rng.randrange(20, 400))
                    start = end - dt.timedelta(days=11 * 365)
                else:
                    start = sub - dt.timedelta(days=rng.randrange(180, 1500))
                    end = start + dt.timedelta(days=11 * 365)
                v.update(issue=start, expiry=end)
            elif doc_type == "affiliation_letter":
                issue = sub - dt.timedelta(days=rng.randrange(60, 500))
                v.update(issue=issue, expiry=issue + dt.timedelta(days=3 * 365), intake=intake)
            elif doc_type == "accreditation_certificate":
                if "EA" in doc_defects:
                    end = sub - dt.timedelta(days=rng.randrange(20, 300))
                    issue = end - dt.timedelta(days=3 * 365)
                else:
                    issue = sub - dt.timedelta(days=rng.randrange(200, 900))
                    end = issue + dt.timedelta(days=3 * 365)
                v.update(issue=issue, expiry=end)
            elif doc_type == "previous_eoa_letter":
                v.update(issue=dt.date.fromisoformat(inst["last_eoa_issue_date"]), expiry=dt.date.fromisoformat(inst["last_eoa_valid_until"]),
                         intake=intake, letter_id=inst["last_eoa_letter_id"])

            qr_img = None
            if doc_type in ("noc", "affiliation_letter"):
                ref_counter[0] += rng.randrange(1, 9)
                pfx = "NOC" if doc_type == "noc" else "AFL"
                ref = f"{pfx}-{inst['region']}-{v['issue'].year}-{ref_counter[0]:07d}"
                v["ref"] = ref
                reg_inst = inst
                if "QW" in doc_defects:
                    reg_inst = D.pick(rng, [i for i in decoys if i["institution_id"] != inst["institution_id"]])
                entry = {"ref_id": ref, "doc_type": doc_type, "institution_id": reg_inst["institution_id"],
                         "institution_name": reg_inst["name"], "issue_date": v["issue"].isoformat(),
                         "valid_until": v["expiry"].isoformat(), "status": "active",
                         "issuer": "MOCK issuer registry", "simulate_outage_attempts": 0}
                if p["clean"] and key in PENDING_CLEAN and doc_type == "noc":
                    entry["status"] = "pending"
                if p["clean"] and key in OUTAGE_CLEAN and doc_type == "affiliation_letter":
                    entry["simulate_outage_attempts"] = OUTAGE_CLEAN[key]
                if "QA" not in doc_defects:
                    registry[ref] = entry
                qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=2)
                qr.add_data(f"https://verify.mock-issuer.invalid/r/{ref}")
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="black", back_color="white")

            spec = D.build_spec(doc_type, v, ctx, rng)
            layout = D.pick(rng, ["L1", "L2", "L3", "L4"])
            fk = D.pick(rng, FONT_KEYS)
            size = D.pick(rng, FONT_SIZES[fk])
            # metadata creation date vs stated issue date
            issue_d = v["issue"]
            if "MI" in doc_defects:
                if rng.random() < 0.5:
                    off = -rng.randrange(20, 400)
                    mkind = "created_before_issue"
                else:
                    off = rng.randrange(38, 500)
                    mkind = "created_long_after_issue"
            else:
                off, mkind = rng.randrange(0, 31), None
            created = ts(issue_d, rng, off)
            data = render_pdf(spec, layout, fk, size, created, qr_img)
            with fitz.open(stream=data, filetype="pdf") as pdoc:
                pages = pdoc.page_count
            (out_dir / "applications" / app_id).mkdir(parents=True, exist_ok=True)
            (out_dir / "applications" / app_id / fname).write_bytes(data)
            fields = {
                "institution_name_written": written, "institution_name_canonical": inst["name"],
                "institution_id": v.get("inst_id"), "issue_date": issue_d.isoformat(),
                "expiry_date": v["expiry"].isoformat() if v.get("expiry") else None,
                "intake": v.get("intake"), "proposed_intake": v.get("proposed"),
                "reference_id": ref, "letter_id": v.get("letter_id"),
            }
            rec.update(sha256=sha(data), fields=fields, layout=layout, font=fk, pages=pages,
                       name_variant=variant, creation_date=pdf_date(created))
            for code in doc_defects:
                detail = {}
                if code == "NM":
                    detail = {"written": written, "canonical": inst["name"], "kind": variant}
                elif code == "IM":
                    detail = {"document_intake": intake, "records_intake": rec_intake}
                elif code == "MI":
                    detail = {"kind": mkind, "offset_days": off, "stated_issue_date": issue_d.isoformat(), "pdf_creation_date": pdf_date(created)}
                elif code in ("QA", "QW"):
                    detail = {"reference_id": ref}
                    if code == "QW":
                        detail["registry_institution_id"] = registry[ref]["institution_id"]
                elif code in ("EL", "EA"):
                    detail = {"expiry_date": v["expiry"].isoformat(), "submission_date": sub.isoformat()}
                defect_labels.append({"defect_type": DEFECT_NAMES[code], "doc_type": doc_type, "detail": detail})
            app_docs.append(dict(rec, bytes=data))

        if missing:
            defect_labels.append({"defect_type": "missing_document", "doc_type": missing, "detail": {}})
        gen[key] = dict(key=key, category=cat, docs=app_docs)
        labels_apps.append({
            "application_id": app_id, "plan_key": key, "category": cat, "split": p["split"],
            "label": "clean" if p["clean"] else "defective", "institution_id": inst["institution_id"],
            "institution_name": inst["name"], "region": inst["region"], "submission_date": sub.isoformat(),
            "required_documents": required, "defects": defect_labels,
            "documents": [{k: v for k, v in d.items() if k != "bytes"} for d in app_docs],
        })

    # decoy registry entries (other institutions' genuine references)
    for i, inst in enumerate(decoys):
        for pfx, dtp in (("NOC", "noc"), ("AFL", "affiliation_letter")):
            ref_counter[0] += rng.randrange(1, 9)
            ref = f"{pfx}-{inst['region']}-2025-{ref_counter[0]:07d}"
            issue = dt.date(2025, rng.randrange(1, 12), rng.randrange(1, 28))
            registry[ref] = {"ref_id": ref, "doc_type": dtp, "institution_id": inst["institution_id"], "institution_name": inst["name"],
                             "issue_date": issue.isoformat(), "valid_until": (issue + dt.timedelta(days=3 * 365)).isoformat(),
                             "status": "active", "issuer": "MOCK issuer registry", "simulate_outage_attempts": 0}

    labels_apps.sort(key=lambda a: a["application_id"])
    labels = {
        "seed": SEED, "note": "ALL DATA SYNTHETIC. Ground truth for evaluation; MOCK records/registry in backend/mock.",
        "split_rule": "Fixed 50/50 application-level split (stratified by clean/defective and category). Tune thresholds on dev only; report test.",
        "defect_types": sorted(DEFECT_NAMES.values()), "applications": labels_apps,
    }
    (out_dir / "labels.json").write_text(json.dumps(labels, indent=2))
    (mock_dir / "records.json").write_text(json.dumps(
        {"_note": "MOCK AICTE institution records (synthetic)", "institutions": sorted(all_records.values(), key=lambda r: r["institution_id"])}, indent=2))
    (mock_dir / "issuer_registry.json").write_text(json.dumps(
        {"_note": "MOCK issuer registry (synthetic)", "entries": sorted(registry.values(), key=lambda r: r["ref_id"])}, indent=2))

    manifest = {}
    for f in sorted((out_dir / "applications").rglob("*.pdf")):
        manifest[str(f.relative_to(out_dir))] = sha(f.read_bytes())
    for f in (out_dir / "labels.json", mock_dir / "records.json", mock_dir / "issuer_registry.json"):
        manifest[f.name] = sha(f.read_bytes())
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return {"labels": labels, "manifest": manifest}


def summarize(labels: dict) -> str:
    apps = labels["applications"]
    lines = [f"applications: {len(apps)}  clean: {sum(a['label']=='clean' for a in apps)}  defective: {sum(a['label']=='defective' for a in apps)}"]
    for sp in ("dev", "test"):
        s = [a for a in apps if a["split"] == sp]
        lines.append(f"  {sp}: {len(s)} apps ({sum(a['label']=='clean' for a in s)} clean / {sum(a['label']=='defective' for a in s)} defective)")
    for dtp in labels["defect_types"]:
        dev = sum(1 for a in apps if a["split"] == "dev" for d in a["defects"] if d["defect_type"] == dtp)
        test = sum(1 for a in apps if a["split"] == "test" for d in a["defects"] if d["defect_type"] == dtp)
        lines.append(f"  {dtp:24s} dev={dev} test={test} total={dev+test}")
    ndocs = sum(len(a["documents"]) for a in apps)
    lines.append(f"documents: {ndocs}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--mock", default=None)
    a = ap.parse_args()
    res = build(a.out, a.mock)
    print(summarize(res["labels"]))
    print("files in manifest:", len(res["manifest"]))
