"""Digital-PDF extraction: text blocks/lines/cells with bounding boxes per page, metadata, QR decoding.

Coordinates are PDF points with origin top-left (PyMuPDF convention), [x0, y0, x1, y1].
`cells` are the unit used for field extraction: words on the same visual row are grouped into
a row (line) and split into cells wherever there is a wide horizontal gap (table columns, label|value).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import cv2
import numpy as np
import pymupdf

MIN_TEXT_CHARS = 20  # a page with fewer extractable characters is treated as having no text layer


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _group_rows(words: list[tuple], tol: float = 0.35) -> list[list[tuple]]:
    """Cluster words into visual rows. Tolerance is a fraction of the *smaller* word height so that
    side-by-side columns with slightly different baselines (sidebar vs main column) are not chained."""
    rows: list[list[tuple]] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        yc = (w[1] + w[3]) / 2
        h = w[3] - w[1]
        for r in rows:
            ry = sum((x[1] + x[3]) / 2 for x in r) / len(r)
            rh = min(x[3] - x[1] for x in r)
            if abs(yc - ry) < tol * min(h, rh):
                r.append(w)
                break
        else:
            rows.append([w])
    for r in rows:
        r.sort(key=lambda w: w[0])
    rows.sort(key=lambda r: sum((x[1] + x[3]) / 2 for x in r) / len(r))
    return rows


def _split_cells(row: list[tuple], gap_min: float = 9.0) -> list[list[tuple]]:
    cells: list[list[tuple]] = [[row[0]]]
    for prev, w in zip(row, row[1:]):
        char_w = max(1.0, (prev[2] - prev[0]) / max(1, len(prev[4])))
        gap = w[0] - prev[2]
        if gap > max(gap_min, 2.2 * char_w):
            cells.append([w])
        else:
            cells[-1].append(w)
    return cells


def _bbox(ws: list[tuple]) -> list[float]:
    return [min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws), max(w[3] for w in ws)]


def words_to_page(words: list[tuple], page_no: int, width: float, height: float, source: str = "text_layer",
                  word_conf: dict | None = None) -> dict:
    """Build the page structure (lines/cells) from word tuples (x0,y0,x1,y1,text,...)."""
    lines = []
    for ri, row in enumerate(_group_rows(words, 0.5 if source == "ocr" else 0.35)):
        cells = []
        for ci, cw in enumerate(_split_cells(row)):
            cells.append({
                "text": " ".join(w[4] for w in cw), "bbox": _bbox(cw), "line": ri, "cell": ci,
                "conf": (float(np.mean([w[5] if len(w) > 5 and isinstance(w[5], float) else 1.0 for w in cw])) if source == "ocr" else 1.0),
            })
        lines.append({"line": ri, "text": "   ".join(c["text"] for c in cells), "bbox": _bbox(row), "cells": cells})
    return {"page": page_no, "width": width, "height": height, "source": source, "lines": lines,
            "text": "\n".join(l["text"] for l in lines)}


def parse_pdf_date(s: str | None) -> str | None:
    """PDF date 'D:YYYYMMDDHHmmSS+00'00'' -> ISO date (YYYY-MM-DD)."""
    if not s:
        return None
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def extract_pdf(data: bytes) -> dict[str, Any]:
    """Return {page_count, has_text_layer, pages:[...], blocks:[...], metadata}.

    has_text_layer is True only if *every* page has a text layer; `pages[i]['has_text']` is per page,
    which lets the orchestrator route individual pages to OCR (M6).
    """
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages, blocks = [], []
    for pno in range(doc.page_count):
        page = doc[pno]
        words = [w for w in page.get_text("words") if w[4].strip()]
        nchars = sum(len(w[4]) for w in words)
        p = words_to_page(words, pno + 1, page.rect.width, page.rect.height)
        p["has_text"] = nchars >= MIN_TEXT_CHARS
        p["n_images"] = len(page.get_images())
        pages.append(p)
        for b in page.get_text("blocks"):
            if b[6] == 0 and b[4].strip():
                blocks.append({"page": pno + 1, "bbox": [b[0], b[1], b[2], b[3]], "text": b[4].strip()})
    md = dict(doc.metadata or {})
    meta = {
        "producer": md.get("producer"), "creator": md.get("creator"), "author": md.get("author"), "title": md.get("title"),
        "creation_date_raw": md.get("creationDate"), "mod_date_raw": md.get("modDate"),
        "creation_date": parse_pdf_date(md.get("creationDate")), "mod_date": parse_pdf_date(md.get("modDate")),
    }
    out = {"page_count": doc.page_count, "has_text_layer": all(p["has_text"] for p in pages) if pages else False,
           "any_text_layer": any(p["has_text"] for p in pages), "pages": pages, "blocks": blocks, "metadata": meta}
    doc.close()
    return out


def render_page_png(data: bytes, page_no: int = 1, dpi: int = 110) -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    pix = doc[page_no - 1].get_pixmap(dpi=dpi)
    out = pix.tobytes("png")
    doc.close()
    return out


def render_page_array(data: bytes, page_no: int = 1, dpi: int = 200) -> np.ndarray:
    doc = pymupdf.open(stream=data, filetype="pdf")
    pix = doc[page_no - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
    doc.close()
    return arr


# ------------------------------------------------------------------ QR
REF_URL_RE = re.compile(r"/r/([A-Z0-9\-]+)$")


def decode_qr_image(gray: np.ndarray, scale_to_pts: float) -> list[dict]:
    """Decode QR codes in a grayscale image. Returns [{payload, ref_id, bbox(pts), method}]."""
    det = cv2.QRCodeDetector()
    results = []
    for variant, img in (("raw", gray),
                         ("otsu", cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]),
                         ("up2x", cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))):
        try:
            val, pts, _ = det.detectAndDecode(img)
        except cv2.error:
            val, pts = "", None
        if val:
            k = 2.0 if variant == "up2x" else 1.0
            p = pts.reshape(-1, 2) / k * scale_to_pts
            bbox = [float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())]
            m = REF_URL_RE.search(val.strip())
            results.append({"payload": val, "ref_id": m.group(1) if m else None, "bbox": bbox, "method": f"opencv_qr:{variant}"})
            break
    return results


def decode_qr_pdf(data: bytes, dpi: int = 200) -> list[dict]:
    """Decode QR codes on every page of a PDF (rendered at `dpi`). Page numbers are 1-based."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    n = doc.page_count
    doc.close()
    out = []
    for p in range(1, n + 1):
        gray = render_page_array(data, p, dpi)
        for r in decode_qr_image(gray, 72.0 / dpi):
            r["page"] = p
            out.append(r)
    return out
