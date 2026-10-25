"""Synthetic tampered-scan generator (M7). Seeded, labelled, synthetic.

    python -m data.generator.tamper

Takes page 1 of existing synthetic scans (levels 1-2), optionally applies one tamper operation, and re-saves EVERY output
(clean controls included) as JPEG quality 95 inside an image-only PDF, so a "was re-saved" artefact cannot by itself separate
tampered from clean files.
Classes: clean | copy_move (a text block pasted over another) | retype (a value whited-out and re-typed in another font) |
erase (a text block whited-out). 16 samples per class; split by the source application's dev/test label.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import cv2
import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "data" / "synthetic"
OUT = SYN / "tamper"
SEED = 42
FINAL_Q = 95
CLASSES = ["clean", "copy_move", "retype", "erase"]
PER_CLASS = 16
DPI = 200


def page_image(pdf: Path) -> tuple[np.ndarray, tuple[float, float]]:
    doc = pymupdf.open(pdf)
    page = doc[0]
    xref = page.get_images()[0][0]
    img = cv2.imdecode(np.frombuffer(doc.extract_image(xref)["image"], np.uint8), cv2.IMREAD_GRAYSCALE)
    size = (page.rect.width, page.rect.height)
    doc.close()
    return img, size


def text_boxes(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Candidate text blocks (x0, y0, x1, y1) found from the image itself."""
    bw = cv2.adaptiveThreshold(cv2.GaussianBlur(gray, (3, 3), 0), 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 14)
    dil = cv2.dilate(bw, cv2.getStructuringElement(cv2.MORPH_RECT, (28, 5)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(dil)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 140 <= w <= 800 and 22 <= h <= 70 and area > 0.35 * w * h:
            out.append((int(x), int(y), int(x + w), int(y + h)))
    return out


def _bg(gray: np.ndarray, b) -> int:
    x0, y0, x1, y1 = b
    ring = np.concatenate([gray[max(0, y0 - 12):y0, x0:x1].ravel(), gray[y1:y1 + 12, x0:x1].ravel()])
    return int(np.median(ring)) if ring.size else 245


def apply(gray: np.ndarray, kind: str, rng: random.Random) -> tuple[np.ndarray, list[list[int]]]:
    img = gray.copy()
    boxes = [b for b in text_boxes(gray) if b[1] > 120]  # skip letterhead band
    if len(boxes) < 4 or kind == "clean":
        return img, []
    rng.shuffle(boxes)
    if kind == "copy_move":
        a, b = boxes[0], next(x for x in boxes[1:] if abs(x[1] - boxes[0][1]) > 60)
        w, h = min(a[2] - a[0], b[2] - b[0]), min(a[3] - a[1], b[3] - b[1])
        img[b[1]:b[1] + h, b[0]:b[0] + w] = gray[a[1]:a[1] + h, a[0]:a[0] + w]
        return img, [[b[0], b[1], b[0] + w, b[1] + h], [a[0], a[1], a[0] + w, a[1] + h]]
    b = boxes[0]
    x0, y0, x1, y1 = b
    img[y0:y1, x0:x1] = _bg(gray, b)
    if kind == "retype":
        txt = "".join(rng.choice("0123456789") for _ in range(rng.randint(4, 9)))
        font = rng.choice([cv2.FONT_HERSHEY_DUPLEX, cv2.FONT_HERSHEY_COMPLEX, cv2.FONT_HERSHEY_TRIPLEX])
        scale = (y1 - y0) / 38.0
        cv2.putText(img, txt, (x0 + 4, y1 - 8), font, scale, int(rng.randint(10, 60)), 2, cv2.LINE_AA)
    return img, [[x0, y0, x1, y1]]


def save_pdf(gray: np.ndarray, size: tuple[float, float], dst: Path) -> None:
    ok, jpg = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, FINAL_Q])
    d = pymupdf.open()
    p = d.new_page(width=size[0], height=size[1])
    p.insert_image(p.rect, stream=jpg.tobytes())
    d.set_metadata({"producer": "Synthetic scanner (MOCK)", "creator": "tamper generator", "title": "", "author": ""})
    dst.parent.mkdir(parents=True, exist_ok=True)
    d.save(dst, deflate=True, garbage=3)
    d.close()


def main() -> None:
    labels = {a["application_id"]: a for a in json.loads((SYN / "labels.json").read_text())["applications"]}
    man = json.loads((SYN / "scans" / "manifest.json").read_text())["items"]
    pool = [i for i in man if i["level"] in (1, 2)]
    rng = random.Random(SEED)
    rng.shuffle(pool)
    items, k = [], 0
    by_split = {"dev": {c: 0 for c in CLASSES}, "test": {c: 0 for c in CLASSES}}
    # round-robin classes so each split gets PER_CLASS/2 per class
    for it in pool:
        app = it["scan"].split("/")[2]
        split = labels[app]["split"]
        todo = [c for c in CLASSES if by_split[split][c] < PER_CLASS // 2]
        if not todo:
            continue
        kind = min(todo, key=lambda c: by_split[split][c])
        gray, size = page_image(SYN / it["scan"])
        r = random.Random(int(hashlib.sha256(f"{SEED}|{it['scan']}|{kind}".encode()).hexdigest()[:8], 16))
        img, regions = apply(gray, kind, r)
        if kind != "clean" and not regions:
            continue
        by_split[split][kind] += 1
        k += 1
        fn = f"t{k:03d}_{kind}_L{it['level']}.pdf"
        save_pdf(img, size, OUT / fn)
        scale = 72.0 / DPI
        items.append({"file": fn, "kind": kind, "tampered": kind != "clean", "split": split, "level": it["level"], "source_scan": it["scan"],
                      "application_id": app, "regions_px": regions, "regions_pt": [[round(v * scale, 2) for v in r_] for r_ in regions],
                      "sha256": hashlib.sha256((OUT / fn).read_bytes()).hexdigest()})
    (OUT / "labels.json").write_text(json.dumps({"note": "Synthetic tampered/clean scanned pages (MOCK). All files re-saved at JPEG q=95.",
                                                 "seed": SEED, "final_jpeg_quality": FINAL_Q, "items": items}, indent=1))
    print(len(items), "files;", {s: v for s, v in by_split.items()})


if __name__ == "__main__":
    main()
