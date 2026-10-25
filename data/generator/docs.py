"""Document-content builders for the synthetic EoA corpus (all MOCK / synthetic)."""
from __future__ import annotations

import datetime as dt

DOC_TYPES = ["affidavit", "noc", "lease", "affiliation_letter", "accreditation_certificate", "previous_eoa_letter"]

TITLES = {
    "affidavit": ["AFFIDAVIT", "AFFIDAVIT AND UNDERTAKING", "SWORN AFFIDAVIT"],
    "noc": ["NO OBJECTION CERTIFICATE", "STATE NO-OBJECTION CERTIFICATE", "NOC - NO OBJECTION CERTIFICATE"],
    "lease": ["LEASE DEED", "DEED OF LEASE", "LAND LEASE AGREEMENT"],
    "affiliation_letter": ["LETTER OF AFFILIATION", "AFFILIATION ORDER", "GRANT OF AFFILIATION"],
    "accreditation_certificate": ["CERTIFICATE OF ACCREDITATION", "ACCREDITATION CERTIFICATE"],
    "previous_eoa_letter": ["EXTENSION OF APPROVAL", "EOA LETTER - EXTENSION OF APPROVAL", "APPROVAL LETTER (EXTENSION OF APPROVAL)"],
}

LABELS = {
    "institution_name": ["Name of the Institution", "Institution Name", "Name of Institute", "Institute"],
    "institution_id": ["Institution ID", "Permanent ID", "AICTE Permanent ID", "Institute ID"],
    "issue_date": ["Date of Issue", "Issued on", "Issue Date", "Date"],
    "expiry_date": ["Valid up to", "Valid till", "Expiry Date", "Validity up to"],
    "intake": ["Approved Intake", "Sanctioned Intake", "Current Intake", "Intake"],
    "proposed_intake": ["Proposed Intake", "Requested Intake"],
    "reference_id": ["Reference No.", "Ref. ID", "Verification Ref", "QR Reference"],
    "letter_id": ["EoA Letter No.", "Approval Letter No.", "Letter ID", "F.No."],
}
LEASE_LABELS = {
    "issue_date": ["Date of Execution", "Executed on", "Lease Start Date"],
    "expiry_date": ["Lease End Date", "Lease expires on", "Term ends on"],
}
AFFIDAVIT_LABELS = {"issue_date": ["Date of Execution", "Executed on", "Date of Affidavit"]}

DATE_STYLES = ["%d/%m/%Y", "%d %B %Y", "%B %d, %Y", "%d-%b-%Y", "%d.%m.%Y"]

_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def num_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return (_TENS[n // 10] + " " + _ONES[n % 10]).strip()
    if n < 1000:
        rest = num_words(n % 100)
        return (_ONES[n // 100] + " Hundred" + (" " + rest if rest else "")).strip()
    return str(n)


def fmt_date(d: dt.date, style: int) -> str:
    return d.strftime(DATE_STYLES[style % len(DATE_STYLES)])


def fmt_intake(n: int, rng) -> str:
    k = rng.randrange(3)
    if k == 0:
        return str(n)
    if k == 1:
        return f"{n} seats"
    return f"{num_words(n)} ({n})"


def pick(rng, seq):
    return seq[rng.randrange(len(seq))]


def build_spec(doc_type: str, v: dict, ctx: dict, rng) -> dict:
    """Return a renderer spec. v holds true field values for this document; ctx holds narrative context."""
    ds = rng.randrange(len(DATE_STYLES))  # one date style per document
    name = v["name"]
    city, state = ctx["city"], ctx["state"]
    fields: list[tuple[str, str]] = [(pick(rng, LABELS["institution_name"]), name)]
    if v.get("inst_id"):
        fields.append((pick(rng, LABELS["institution_id"]), v["inst_id"]))
    if doc_type == "lease":
        fields.append((pick(rng, LEASE_LABELS["issue_date"]), fmt_date(v["issue"], ds)))
        fields.append((pick(rng, LEASE_LABELS["expiry_date"]), fmt_date(v["expiry"], ds)))
    else:
        il = AFFIDAVIT_LABELS["issue_date"] if doc_type == "affidavit" else LABELS["issue_date"]
        fields.append((pick(rng, il), fmt_date(v["issue"], ds)))
        if v.get("expiry"):
            fields.append((pick(rng, LABELS["expiry_date"]), fmt_date(v["expiry"], ds)))
    if v.get("intake") is not None:
        fields.append((pick(rng, LABELS["intake"]), fmt_intake(v["intake"], rng)))
    if v.get("proposed") is not None:
        fields.append((pick(rng, LABELS["proposed_intake"]), fmt_intake(v["proposed"], rng)))
    if v.get("letter_id"):
        fields.append((pick(rng, LABELS["letter_id"]), v["letter_id"]))
    if v.get("ref"):
        fields.append((pick(rng, LABELS["reference_id"]), v["ref"]))
    if rng.random() < 0.5:  # vary vertical order of the particulars (name stays first)
        head, tail = fields[:1], fields[1:]
        rng.shuffle(tail)
        fields = head + tail

    signer = ctx["signer"]
    issuer: list[str]
    pre: list[str] = []
    post: list[str] = []
    course = ctx.get("course", "B.E. Computer Science and Engineering")

    if doc_type == "affidavit":
        issuer = [f"Before the Notary Public, {city}", "Non-judicial stamp paper (specimen)"]
        pre = [f"I, {signer[0]}, {signer[1]} of {name}, do hereby solemnly affirm and declare as follows:",
               f"1. That the institution is located at {city}, {state}, and is administered by its governing trust.",
               "2. That all particulars furnished in the application for Extension of Approval for the academic year 2026-27 are true and correct to the best of my knowledge and belief."]
        cat = ctx["category"]
        if cat == "eoa_renewal_basic":
            pre.append("3. That the institution seeks renewal of its existing approval and has not altered its land, buildings or infrastructure since the last approval.")
        elif cat == "eoa_new_course":
            pre.append(f"3. That the institution seeks approval for the introduction of a new programme, {course}, as detailed in the application.")
        else:
            pre.append("3. That the institution seeks a change in sanctioned intake and undertakes to provide the additional faculty and laboratory facilities required.")
        if v.get("prev_date"):
            pre.append(f"4. That the previous approval of the institution was extended on {fmt_date(v['prev_date'], rng.randrange(5))}.")
        post = [f"Verified at {city} that the contents of this affidavit are true and correct and nothing material has been concealed.",
                f"Attested by {ctx['notary']}, Notary Public."]
        title = pick(rng, TITLES[doc_type])
        meta_author = ctx["notary"]
    elif doc_type == "noc":
        issuer = [f"Directorate of Technical Education, {state}", "(synthetic issuing authority)"]
        pre = [f"This is to certify that the State Government has no objection to the continued operation of {name}, {city}, {state}, as a technical institution offering engineering programmes, subject to compliance with applicable norms.",
               "This certificate is issued on the request of the institution for the purpose of seeking Extension of Approval from the national regulator."]
        post = ["This certificate is valid for the period stated above unless withdrawn earlier. Its authenticity may be verified using the QR code or reference printed on this document."]
        title = pick(rng, TITLES[doc_type])
        meta_author = f"Directorate of Technical Education, {state}"
    elif doc_type == "lease":
        issuer = ["Registered Lease Instrument", f"Sub-Registrar Office, {city}"]
        clauses = [
            f"This Deed of Lease is made at {city}, {state}, between {ctx['lessor']} (the Lessor) and {name} (the Lessee).",
            f"1. The Lessor leases to the Lessee the land bearing Survey No. {ctx['survey']} measuring {ctx['acres']} acres situated at {city} for the purpose of running a technical education institution.",
            "2. The Lessee shall pay an annual lease rent as agreed between the parties, payable in advance on or before the 10th day of April each year.",
            "3. The Lessee shall use the demised land and the buildings thereon exclusively for educational purposes and shall not sub-lease or assign any part thereof without the written consent of the Lessor.",
            "4. The Lessee shall maintain the premises in good condition, pay all taxes and charges, and comply with all applicable building and safety regulations.",
            "5. The Lessor covenants that the Lessee shall peaceably hold and enjoy the demised premises during the term of this lease without interruption.",
            "6. Either party may terminate this lease only for breach of a material covenant, after giving ninety days notice in writing and an opportunity to remedy the breach.",
            "7. On expiry or earlier determination, the Lessee shall deliver vacant possession of the land to the Lessor, subject to the right of renewal on mutually agreed terms.",
            "8. All disputes arising out of this deed shall be referred to arbitration in accordance with the law for the time being in force.",
            "9. The stamp duty and registration charges on this deed have been borne by the Lessee.",
            "10. This deed is executed in duplicate, each copy being treated as an original.",
            "11. The Lessee shall be entitled to raise permanent structures on the demised land for classrooms, laboratories, hostels, libraries, sports facilities and ancillary buildings, in accordance with sanctioned plans.",
            "12. The Lessee shall insure all buildings against fire and allied perils and shall furnish the policy particulars to the Lessor on request.",
            "13. The Lessor shall execute such further documents and give such assurances as may be reasonably required by the Lessee for the purpose of obtaining statutory approvals from the regulator, the affiliating university and the State Government.",
            "14. Any notice required under this deed shall be in writing and shall be deemed to be served when sent by registered post to the address of the party first written above.",
            "15. The rent shall be revised once in every three years by a maximum of five per cent over the rent payable immediately before such revision.",
            "16. Failure of either party to enforce any provision of this deed shall not constitute a waiver of the right to enforce that provision thereafter.",
            "17. The Lessee shall not create any mortgage, charge or encumbrance over the leasehold interest without the prior written consent of the Lessor.",
            "18. In witness whereof the parties have set their hands on the date and at the place first written above in the presence of the witnesses named below.",
        ]
        pre = clauses[:9]
        post = clauses[9:]
        title = pick(rng, TITLES[doc_type])
        meta_author = ctx["lessor"]
    elif doc_type == "affiliation_letter":
        issuer = [ctx["university"], "(synthetic affiliating university)"]
        pre = [f"The University is pleased to grant affiliation to {name}, {city}, {state}, for the academic year 2026-27 for the engineering programmes mentioned in its application, subject to the conditions of affiliation.",
               "The institution shall maintain the prescribed faculty strength, laboratory facilities and library resources throughout the period of affiliation."]
        post = ["This letter may be verified using the QR code or the reference printed on it. Any change in the particulars above requires prior approval of the University."]
        title = pick(rng, TITLES[doc_type])
        meta_author = ctx["university"]
    elif doc_type == "accreditation_certificate":
        issuer = ["National Board for Programme Accreditation", "(synthetic accrediting body)"]
        pre = [f"This is to certify that the engineering programmes of {name}, {city}, {state}, have been assessed and accredited in accordance with the prescribed norms.",
               "The accreditation is valid for the period stated above, subject to continued compliance and satisfactory annual monitoring."]
        post = ["This certificate is the property of the accrediting body and must be returned on demand."]
        title = pick(rng, TITLES[doc_type])
        meta_author = "National Board for Programme Accreditation (synthetic)"
    elif doc_type == "previous_eoa_letter":
        issuer = [f"Regional Office - {ctx['region']}", "Council for Technical Education (synthetic specimen)"]
        pre = [f"Approval is hereby extended to {name}, {city}, {state}, for the academic year 2025-26 for the programmes and intake indicated below, subject to the conditions prescribed in the Approval Process Handbook.",
               "The institution shall display the details of this approval on its website and shall not admit students in excess of the approved intake."]
        post = ["This is a system generated letter issued on behalf of the Regional Officer."]
        title = pick(rng, TITLES[doc_type])
        meta_author = f"Regional Office - {ctx['region']} (synthetic)"
    else:
        raise ValueError(doc_type)

    return {
        "title": title, "issuer": issuer, "fields": fields, "pre": pre, "post": post,
        "signer": signer, "meta_title": f"{title} - {name}", "meta_author": meta_author,
    }


# --------------------------------------------------------------- name variants
ABBREV = [("Institute", "Inst."), ("Engineering", "Engg."), ("College", "Coll."), ("Technology", "Tech."), ("and", "&")]


def benign_variant(name: str, city: str, rng) -> tuple[str, str]:
    """Return (written_name, variant_kind). Variants must NOT change institution identity."""
    k = rng.randrange(7)
    if k == 0:
        return name, "exact"
    if k == 1:
        return name.upper(), "upper"
    if k == 2:
        out = name
        for a, b in ABBREV:
            if a in out and rng.random() < 0.7:
                out = out.replace(a, b)
        return (out if out != name else name.replace("Institute", "Inst.")), "abbrev"
    if k == 3:
        return f"{name}, {city}", "city_suffix"
    if k == 4:
        return f"{name} (Autonomous)", "autonomous"
    if k == 5:
        return f"M/s {name}", "ms_prefix"
    return name.replace(" and ", " & "), "ampersand"
