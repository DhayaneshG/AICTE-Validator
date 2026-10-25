"""Field extraction from page structures (digital text layer or OCR).

Strategy: find a *label cell* (known vocabulary), then take the value from (a) the same cell after ':',
(b) the next cell on the row, or (c) the cell(s) directly below (stacked layouts). Distinctive identifiers
(institution ID, letter ID, reference ID) also have a lower-confidence regex fallback over the page text.
Every field is {value, confidence, page, bbox, method, raw}. Confidence = method prior x format validity x
mean OCR confidence of the value cells (1.0 for a real text layer).
"""
from __future__ import annotations

import re
from typing import Optional

from .types import FieldValue

# Known label vocabulary (maintained separately from the generator; a real deployment would grow this list).
FIELD_LABELS: dict[str, list[str]] = {
    "institution_name": ["Name of the Institution", "Institution Name", "Name of Institute", "Institute"],
    "institution_id": ["Institution ID", "Permanent ID", "AICTE Permanent ID", "Institute ID"],
    "issue_date": ["Date of Issue", "Issued on", "Issue Date", "Date", "Date of Execution", "Executed on",
                   "Date of Affidavit", "Lease Start Date"],
    "expiry_date": ["Valid up to", "Valid till", "Expiry Date", "Validity up to", "Lease End Date",
                    "Lease expires on", "Term ends on"],
    "intake": ["Approved Intake", "Sanctioned Intake", "Current Intake", "Intake"],
    "proposed_intake": ["Proposed Intake", "Requested Intake"],
    "reference_id": ["Reference No.", "Ref. ID", "Verification Ref", "QR Reference"],
    "letter_id": ["EoA Letter No.", "Approval Letter No.", "Letter ID", "F.No."],
}
ALL_FIELDS = list(FIELD_LABELS)
_LABEL_INDEX = sorted(((lab.lower(), f) for f, labs in FIELD_LABELS.items() for lab in labs), key=lambda t: -len(t[0]))

INST_ID_RE = re.compile(r"(?<![\w/])1-\d{11}(?![\w/])")
LETTER_ID_RE = re.compile(r"(?<![\w/])[A-Z]{2,4}/1-\d{11}/\d{4}/EOA(?![\w/])")
REF_ID_RE = re.compile(r"(?<![\w-])[A-Z]{3}-[A-Z]{2,4}-\d{4}-\d{7}(?![\w-])")
# OCR-tolerant forms of the identifiers: stray spaces around separators, O/0 and I/l/1 confusions.
_D = r"[\dOIl]"
_TOL = [
    re.compile(rf"(?<![\w/])([A-Z0]{{2,4}})\s*/\s*(1\s*-\s*{_D}{{11}})\s*/\s*({_D}{{4}})\s*/\s*(E[O0]A)(?![\w/])"),
    re.compile(rf"(?<![\w-])([A-Z0]{{3}})\s*-\s*([A-Z0]{{2,4}})\s*-\s*({_D}{{4}})\s*-\s*({_D}{{7}})(?![\w-])"),
    re.compile(rf"(?<![\w/])(1\s*-\s*{_D}{{11}})(?![\w/])"),
]


def _num(x: str) -> str:
    return re.sub(r"\s+", "", x).replace("O", "0").replace("I", "1").replace("l", "1")


def _alpha(x: str) -> str:
    return x.replace("0", "O")


def repair_ids(text: str) -> str:
    """Rewrite OCR-damaged identifiers into canonical form (no-op on clean text)."""
    text = _TOL[0].sub(lambda m: f"{_alpha(m.group(1))}/{_num(m.group(2))}/{_num(m.group(3))}/EOA", text)
    text = _TOL[1].sub(lambda m: f"{_alpha(m.group(1))}-{_alpha(m.group(2))}-{_num(m.group(3))}-{_num(m.group(4))}", text)
    return _TOL[2].sub(lambda m: _num(m.group(1)), text)


MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_date(s: str) -> Optional[str]:
    """Parse the date formats seen in Indian official documents to ISO (DD/MM/YYYY convention)."""
    s = s.strip().replace("O", "0") if re.fullmatch(r"[\dO/.\- ]+", s.strip()) else s.strip()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", s)
    if m:
        y, mo, d = map(int, m.groups())
        return _iso(y, mo, d)
    m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b", s)
    if m:
        d, mo, y = map(int, m.groups())
        return _iso(y, mo, d)
    m = re.search(r"\b(\d{1,2})[ \-]([A-Za-z]{3,9})[ \-,]+(\d{4})\b", s)
    if m and m.group(2)[:3].lower() in MONTHS:
        return _iso(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    m = re.search(r"\b([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})\b", s)
    if m and m.group(1)[:3].lower() in MONTHS:
        return _iso(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
    return None


def _iso(y, m, d) -> Optional[str]:
    import datetime as dt
    try:
        return dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def _match_label(text: str):
    """Return (field, remainder) if the cell text is a known label (optionally 'Label : value')."""
    t = text.strip()
    low = t.lower()
    for lab, field in _LABEL_INDEX:
        if low.rstrip(" :") == lab or re.sub(r"\s+", "", low.rstrip(" :")) == lab.replace(" ", ""):  # OCR may split/merge spaces
            return field, ""
        if low.startswith(lab):
            rest = t[len(lab):].lstrip()
            if rest.startswith(":"):
                return field, rest[1:].strip()
    return None


def _parse(field: str, raw: str):
    raw = raw.strip()
    if field in ("institution_id", "letter_id", "reference_id"):
        raw = repair_ids(raw)
    if field in ("issue_date", "expiry_date"):
        v = parse_date(raw)
        return v, (1.0 if v else 0.0)
    if field in ("intake", "proposed_intake"):
        m = re.search(r"\b(\d{2,4})\b", raw.replace("O", "0") if re.fullmatch(r"[\dO ()a-zA-Z]+", raw) else raw)
        return (int(m.group(1)), 1.0) if m else (None, 0.0)
    if field == "institution_id":
        m = INST_ID_RE.search(raw)
        return (m.group(0), 1.0) if m else (None, 0.0)
    if field == "letter_id":
        m = LETTER_ID_RE.search(raw)
        return (m.group(0), 1.0) if m else (None, 0.0)
    if field == "reference_id":
        m = REF_ID_RE.search(raw)
        return (m.group(0), 1.0) if m else (None, 0.0)
    if field == "institution_name":
        v = re.sub(r"\s+", " ", raw).strip(" :")
        return (v, 1.0) if len(v) >= 6 else (None, 0.0)
    return raw, 1.0


METHOD_PRIOR = {"label_same_cell": 0.97, "label_right_cell": 0.97, "label_below_cell": 0.93, "regex_fallback": 0.70}


def _union(bs: list[list[float]]) -> list[float]:
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


NAME_HINT_RE = re.compile(r"institut|inst\.|college|coll\.|engineering|engg|technolog|tech\.|school|polytechnic|university|academy|sciences", re.I)


def _valid_value(field: str, raw: str) -> bool:
    value, valid = _parse(field, raw)
    if not valid or value is None:
        return False
    if field == "institution_name":
        return bool(NAME_HINT_RE.search(raw)) and len(raw) <= 140
    return True


def _continuations(p: dict, first: dict, last: dict, label_lines: set, pi: int) -> list[dict]:
    """Wrapped continuation lines of a (long) value: same left edge, tight vertical gap, no label on the row."""
    out = []
    for ln2 in p["lines"]:
        if ln2["bbox"][1] < last["bbox"][1] - 1 or ln2["line"] <= last.get("line", -1):
            continue
        hit = None
        for c2 in ln2["cells"]:
            if (abs(c2["bbox"][0] - first["bbox"][0]) <= 6 and -2 <= c2["bbox"][1] - last["bbox"][3] <= 4
                    and (pi, ln2["line"]) not in label_lines and not _match_label(c2["text"])):
                hit = c2
                break
        if hit:
            out.append(hit)
            last = hit
        elif ln2["bbox"][1] - last["bbox"][3] > 8:
            break
    return out


def extract_fields(pages: list[dict], fields: list[str] | None = None) -> dict[str, FieldValue]:
    """Extract known fields from a document's pages (as produced by extract.words_to_page)."""
    wanted = fields or ALL_FIELDS
    out: dict[str, FieldValue] = {}
    labels = []  # (page_idx, line_idx, cell_idx, field, remainder)
    for pi, p in enumerate(pages):
        for ln in p["lines"]:
            for ci, c in enumerate(ln["cells"]):
                m = _match_label(c["text"])
                if m:
                    labels.append((pi, ln["line"], ci, m[0], m[1]))
    label_lines = {(pi, li) for pi, li, ci, _, _ in labels}

    for pi, li, ci, field, remainder in labels:
        if field not in wanted:
            continue
        p = pages[pi]
        ln = p["lines"][li]
        cell = ln["cells"][ci]
        # candidate value cells, in priority order
        cands: list[tuple[str, list[dict]]] = []
        if remainder:
            cands.append(("label_same_cell", [{"text": remainder, "bbox": cell["bbox"], "conf": cell.get("conf", 1.0), "line": li}]))
        if ci + 1 < len(ln["cells"]) and not _match_label(ln["cells"][ci + 1]["text"]):
            cands.append(("label_right_cell", [ln["cells"][ci + 1]]))
        best = None
        for ln2 in p["lines"][li + 1: li + 4]:
            for c2 in ln2["cells"]:
                dx = abs(c2["bbox"][0] - cell["bbox"][0])
                dy = c2["bbox"][1] - cell["bbox"][3]
                if dx <= 18 and -2 <= dy <= 16 and not _match_label(c2["text"]):
                    if best is None or dy < best[0]:
                        best = (dy, c2)
            if best:
                break
        if best:
            cands.append(("label_below_cell", [best[1]]))
        chosen = None
        for method, vcs in cands:
            if field == "institution_name":
                vcs = vcs + _continuations(p, vcs[0], vcs[-1], label_lines, pi)
            raw = " ".join(c["text"] for c in vcs)
            if _valid_value(field, raw):
                chosen = (method, vcs, raw)
                break
        if chosen is None and cands:
            method, vcs = cands[0]
            chosen = (method, vcs, " ".join(c["text"] for c in vcs))
        if chosen is None:
            continue
        method, val_cells, raw = chosen
        value, valid = _parse(field, raw)
        if field == "institution_name" and value is not None and not NAME_HINT_RE.search(raw):
            valid = 0.4  # name-like vocabulary absent -> low confidence (routes to manual review)
        ocr_conf = sum(c.get("conf", 1.0) for c in val_cells) / len(val_cells)
        conf = round(METHOD_PRIOR[method] * (valid if valid else 0.2) * ocr_conf, 3)
        fv = FieldValue(name=field, value=value, confidence=conf, page=p["page"], bbox=_union([c["bbox"] for c in val_cells]),
                        method=method, raw=raw, label=cell["text"].strip().rstrip(":").strip())
        if field not in out or fv.confidence > out[field].confidence:
            out[field] = fv

    # regex fallbacks for distinctive identifiers
    for field, rx in (("institution_id", INST_ID_RE), ("letter_id", LETTER_ID_RE), ("reference_id", REF_ID_RE)):
        if field in wanted and (field not in out or out[field].value is None):
            for p in pages:
                m = rx.search(repair_ids(p["text"]))
                if m:
                    bbox = None
                    for ln in p["lines"]:
                        for c in ln["cells"]:
                            if m.group(0) in repair_ids(c["text"]):
                                bbox = c["bbox"]
                    ocr = min([c.get("conf", 1.0) for ln in p["lines"] for c in ln["cells"] if m.group(0) in repair_ids(c["text"])] or [1.0])
                    out[field] = FieldValue(name=field, value=m.group(0), confidence=round(METHOD_PRIOR["regex_fallback"] * ocr, 3),
                                            page=p["page"], bbox=bbox, method="regex_fallback", raw=m.group(0))
                    break
    return out
