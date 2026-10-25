"""Image-forensic tamper INDICATORS for scanned pages (advisory; severity capped at medium).

Detectors:  ela          error-level analysis (JPEG re-compression residual, block statistics)
            noise        regions whose sensor/compression noise is far below the page norm (flat fills / erasures)
            copy_move    large near-identical image blocks at two different places on the page
            text_line    ink strokes inside a line that are inconsistent with the rest of that line
Each detector returns regions [{type, page, bbox (PDF points), score, ...}]. Scores are NOT probabilities.
Digital (text-layer) pages are not analysed: they have no image to examine.
"""
from __future__ import annotations

import io
from typing import Optional

import cv2
import numpy as np
import pymupdf

DEFAULTS = {  # DEV-tuned values live in backend/config/thresholds.json ("tamper"); these are only fallbacks
    "ela_quality": 90, "ela_block": 32, "ela_z": 30.0, "ela_min_blocks": 3,
    "noise_block": 16, "noise_z": -2.5, "noise_min_blocks": 10, "noise_ink_max": 0.01,
    "cm_patch_w": 160, "cm_patch_h": 24, "cm_ncc": 0.99, "cm_min_ink": 0.05, "cm_max_patches": 250, "cm_min_components": 7,
    "tl_z": 4.0,
    "severity": {"ela": "low", "noise": "medium", "copy_move": "medium", "text_line": "low"},
    "max_per_type": 2,  # report only the strongest indicators of each type per page (keeps the review queue readable)
}


def page_images(data: bytes) -> list[dict]:
    """Embedded page images of pages that are pure scans: [{page, gray, width_pt, height_pt, jpeg}]."""
    out = []
    doc = pymupdf.open(stream=data, filetype="pdf")
    for pno in range(doc.page_count):
        page = doc[pno]
        imgs = page.get_images()
        if not imgs or len(page.get_text("words")) >= 5:
            continue
        info = doc.extract_image(imgs[0][0])
        raw = info["image"]
        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        if arr is None:
            continue
        out.append({"page": pno + 1, "gray": arr, "width_pt": page.rect.width, "height_pt": page.rect.height,
                    "is_jpeg": info.get("ext") in ("jpeg", "jpg"), "raw": raw})
    doc.close()
    return out


def _blocks(a: np.ndarray, b: int, fn) -> np.ndarray:
    h, w = a.shape
    hh, ww = h // b, w // b
    out = np.zeros((hh, ww), np.float32)
    for i in range(hh):
        for j in range(ww):
            out[i, j] = fn(a[i * b:(i + 1) * b, j * b:(j + 1) * b])
    return out


def _robust_z(x: np.ndarray, mask: Optional[np.ndarray] = None) -> np.ndarray:
    v = x[mask] if mask is not None and mask.any() else x.ravel()
    med = np.median(v)
    mad = np.median(np.abs(v - med)) * 1.4826 + 1e-6
    return (x - med) / mad


def _clusters(flag: np.ndarray, min_blocks: int) -> list[tuple[int, int, int, int, int]]:
    n, _, stats, _ = cv2.connectedComponentsWithStats(flag.astype(np.uint8), connectivity=8)
    return [(int(s[0]), int(s[1]), int(s[0] + s[2]), int(s[1] + s[3]), int(s[4])) for s in stats[1:] if s[4] >= min_blocks]


def _to_pts(box_px, b, sx, sy):
    x0, y0, x1, y1 = box_px
    return [round(x0 * b * sx, 2), round(y0 * b * sy, 2), round(x1 * b * sx, 2), round(y1 * b * sy, 2)]


def ela_map(gray: np.ndarray, quality: int) -> np.ndarray:
    ok, enc = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, quality])
    rec = cv2.imdecode(enc, cv2.IMREAD_GRAYSCALE)
    return cv2.GaussianBlur(np.abs(gray.astype(np.float32) - rec.astype(np.float32)), (5, 5), 0)


def detect_ela(gray: np.ndarray, p: dict) -> tuple[list[dict], float]:
    b = p["ela_block"]
    e = ela_map(gray, p["ela_quality"])
    ink = _blocks((gray < 140).astype(np.float32), b, np.mean)
    m = _blocks(e, b, np.mean)
    z = np.zeros_like(m)
    for lo, hi in ((0, 0.005), (0.005, 0.05), (0.05, 1.01)):  # compare blocks of similar ink content only
        sel = (ink >= lo) & (ink < hi)
        if sel.sum() > 20:
            z[sel] = _robust_z(m, sel)[sel]
    flag = z > p["ela_z"]
    regs = [{"box": c[:4], "score": float(z[c[1]:c[3], c[0]:c[2]].max()), "blocks": c[4]} for c in _clusters(flag, p["ela_min_blocks"])]
    return regs, float(z.max())


def detect_noise(gray: np.ndarray, p: dict) -> tuple[list[dict], float]:
    b = p["noise_block"]
    hp = cv2.Laplacian(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_32F)
    ink = _blocks((gray < 150).astype(np.float32), b, np.mean)
    s = _blocks(np.abs(hp), b, np.mean)
    paper = ink <= p["noise_ink_max"]
    if paper.sum() < 50:
        return [], 0.0
    z = _robust_z(s, paper)
    z[~paper] = 0
    flag = (z < p["noise_z"]) & paper
    regs = [{"box": c[:4], "score": float(-z[c[1]:c[3], c[0]:c[2]].min()), "blocks": c[4]} for c in _clusters(flag, p["noise_min_blocks"])]
    return regs, float(-z.min())


def detect_copy_move(gray: np.ndarray, p: dict) -> tuple[list[dict], float]:
    """Find an ink-rich patch whose pixels re-appear (NCC >= threshold) at a different, non-overlapping place."""
    h, w = gray.shape
    pw, ph = p["cm_patch_w"], p["cm_patch_h"]
    s = 0.5  # work at half resolution
    g = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    pw2, ph2 = int(pw * s), int(ph * s)
    ink = (g < 150)
    ys, xs = np.mgrid[0:g.shape[0] - ph2:ph2 // 2, 0:g.shape[1] - pw2:pw2 // 2]
    cand = [(int(y), int(x)) for y, x in zip(ys.ravel(), xs.ravel()) if ink[y:y + ph2, x:x + pw2].mean() >= p["cm_min_ink"]]
    rng = np.random.default_rng(0)
    if len(cand) > p["cm_max_patches"]:
        cand = [cand[i] for i in rng.choice(len(cand), p["cm_max_patches"], replace=False)]
    best = 0.0
    regs = []
    for y, x in cand:
        tpl = g[y:y + ph2, x:x + pw2]
        if tpl.std() < 8 or cv2.connectedComponents((tpl < 150).astype(np.uint8))[0] < p["cm_min_components"]:
            continue  # flat paper, or rules/borders (which legitimately repeat); need glyph-like content
        res = cv2.matchTemplate(g, tpl, cv2.TM_CCOEFF_NORMED)
        res[max(0, y - ph2):y + ph2, max(0, x - pw2):x + pw2] = -1  # ignore the patch itself and its overlaps
        _, mx, _, loc = cv2.minMaxLoc(res)
        best = max(best, float(mx))
        if mx >= p["cm_ncc"]:
            regs.append({"box_px": [loc[0] / s, loc[1] / s, (loc[0] + pw2) / s, (loc[1] + ph2) / s],
                         "src_px": [x / s, y / s, (x + pw2) / s, (y + ph2) / s], "score": float(mx)})
    return regs, best


def detect_text_line(gray: np.ndarray, p: dict) -> tuple[list[dict], float]:
    """Stroke-width consistency inside a text line: a word whose stroke width is an outlier within its own line."""
    bw = cv2.adaptiveThreshold(cv2.GaussianBlur(gray, (3, 3), 0), 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 14)
    dil = cv2.dilate(bw, cv2.getStructuringElement(cv2.MORPH_RECT, (28, 3)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dil)
    dist = cv2.distanceTransform(bw, cv2.DIST_L2, 3)
    regs, best = [], 0.0
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if not (18 <= h <= 80 and w >= 100):
            continue
        sub = (lab[y:y + h, x:x + w] == i) & (bw[y:y + h, x:x + w] > 0)
        if sub.sum() < 200:
            continue
        # per word-ish chunk: split by empty columns > 14px
        cols = sub.any(axis=0)
        runs, start, gap = [], None, 0
        for j, c in enumerate(cols):
            if c:
                if start is None:
                    start = j
                gap = 0
            elif start is not None:
                gap += 1
                if gap > 14:
                    runs.append((start, j - gap)); start = None
        if start is not None:
            runs.append((start, len(cols) - 1))
        sw = []
        for (a0, a1) in runs:
            m = sub[:, a0:a1 + 1]
            d = dist[y:y + h, x + a0:x + a1 + 1][m]
            sw.append(float(np.percentile(d, 90)) if d.size > 30 else np.nan)
        sw = np.array(sw)
        ok = ~np.isnan(sw)
        if ok.sum() < 3:
            continue
        z = np.abs(sw - np.nanmedian(sw)) / (np.nanmedian(np.abs(sw - np.nanmedian(sw))) * 1.4826 + 0.25)
        k = int(np.nanargmax(z))
        best = max(best, float(z[k]))
        if z[k] > p["tl_z"]:
            a0, a1 = runs[k]
            regs.append({"box_px": [x + a0, y, x + a1, y + h], "score": float(z[k])})
    return regs, best


def analyze_page(img: dict, params: Optional[dict] = None) -> list[dict]:
    p = {**DEFAULTS, **(params or {})}
    g = img["gray"]
    h, w = g.shape
    sx, sy = img["width_pt"] / w, img["height_pt"] / h
    out = []
    regs, _ = detect_ela(g, p)
    out += [{"type": "ela", "bbox": _to_pts(r["box"], p["ela_block"], sx, sy), "score": round(r["score"], 2), "detail": f"{r['blocks']} blocks"} for r in regs]
    regs, _ = detect_noise(g, p)
    out += [{"type": "noise", "bbox": _to_pts(r["box"], p["noise_block"], sx, sy), "score": round(r["score"], 2), "detail": f"{r['blocks']} blocks"} for r in regs]
    regs, _ = detect_copy_move(g, p)
    for r in regs:
        b = r["box_px"]
        out.append({"type": "copy_move", "bbox": [round(b[0] * sx, 2), round(b[1] * sy, 2), round(b[2] * sx, 2), round(b[3] * sy, 2)],
                    "score": round(r["score"], 3), "detail": "near-identical block elsewhere on the page",
                    "source_bbox": [round(r["src_px"][0] * sx, 2), round(r["src_px"][1] * sy, 2), round(r["src_px"][2] * sx, 2), round(r["src_px"][3] * sy, 2)]})
    regs, _ = detect_text_line(g, p)
    for r in regs:
        b = r["box_px"]
        out.append({"type": "text_line", "bbox": [round(b[0] * sx, 2), round(b[1] * sy, 2), round(b[2] * sx, 2), round(b[3] * sy, 2)],
                    "score": round(r["score"], 2), "detail": "stroke width differs from the rest of the line"})
    keep = []
    for typ in {o["type"] for o in out}:
        keep += sorted((o for o in out if o["type"] == typ), key=lambda o: -o["score"])[:p["max_per_type"]]
    out = sorted(keep, key=lambda o: (o["type"], -o["score"]))
    for o in out:
        o["page"] = img["page"]
        o["severity"] = p["severity"].get(o["type"], "low")
    return out


def analyze_pdf(data: bytes, params: Optional[dict] = None) -> list[dict]:
    res = []
    for img in page_images(data):
        res += analyze_page(img, params)
    return res
