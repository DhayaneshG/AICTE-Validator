"""Synthetic scan generator (M6). Turns digital synthetic PDFs into image-only PDFs at 3 degradation levels.

    python -m data.generator.scans            # seed 42; 8 dev + 8 test applications x 3 levels

Everything is seeded (python/numpy random reseeded per file) and labelled synthetic. Output:
    data/synthetic/scans/L{1,2,3}/APP-xxxx/upload_n.pdf   image-only PDFs
    data/synthetic/scans/manifest.json                    source file, level, parameters, sha256
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "data" / "synthetic"
OUT = SYN / "scans"
DPI = 200
SEED = 42
N_PER_SPLIT = 8

LEVELS = {
    1: {"name": "light", "rot": 0.5, "blur": 0, "jpeg": (80, 92)},
    2: {"name": "medium", "rot": 1.5, "blur": 3, "jpeg": (55, 75)},
    3: {"name": "heavy", "rot": 3.0, "blur": 3, "jpeg": (30, 45)},
}


def _pipeline(level: int):
    from augraphy import AugraphyPipeline, Brightness, Gamma, InkBleed, LightingGradient, SubtleNoise
    if level == 1:
        post = [Brightness((0.95, 1.05), p=1), SubtleNoise(subtle_range=5, p=1)]
        ink = []
    elif level == 2:
        post = [LightingGradient(transparency=0.3, p=1), Gamma((0.9, 1.1), p=1), SubtleNoise(subtle_range=10, p=1)]
        ink = [InkBleed(intensity_range=(0.1, 0.2), p=1)]
    else:
        post = [LightingGradient(transparency=0.5, p=1), Gamma((0.8, 1.2), p=1), SubtleNoise(subtle_range=18, p=1)]
        ink = [InkBleed(intensity_range=(0.2, 0.35), p=1)]
    return AugraphyPipeline(ink_phase=ink, paper_phase=[], post_phase=post)


def degrade(gray: np.ndarray, level: int, rng: random.Random) -> tuple[np.ndarray, dict]:
    cfg = LEVELS[level]
    random.seed(rng.randrange(2**31))
    np.random.seed(rng.randrange(2**31))
    angle = rng.uniform(-cfg["rot"], cfg["rot"])
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    M[:, 2] += (rng.uniform(-6, 6) * level, rng.uniform(-6, 6) * level)
    rot = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    if cfg["blur"]:
        rot = cv2.GaussianBlur(rot, (cfg["blur"], cfg["blur"]), 0)
    out = _pipeline(level)(cv2.cvtColor(rot, cv2.COLOR_GRAY2BGR))
    out = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY) if out.ndim == 3 else out
    q = rng.randint(*cfg["jpeg"])
    return out, {"rotation_deg": round(angle, 3), "jpeg_quality": q}


def make_scan(src: Path, dst: Path, level: int, seed: int) -> dict:
    rng = random.Random(seed)
    sdoc = pymupdf.open(src)
    ndoc = pymupdf.open()
    params = []
    for page in sdoc:
        pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
        img, p = degrade(gray, level, rng)
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, p["jpeg_quality"]])
        np_ = ndoc.new_page(width=page.rect.width, height=page.rect.height)
        np_.insert_image(np_.rect, stream=jpg.tobytes())
        params.append(p)
    ndoc.set_metadata({"producer": "Synthetic scanner (MOCK)", "creator": "scan generator", "title": "", "author": ""})
    dst.parent.mkdir(parents=True, exist_ok=True)
    ndoc.save(dst, deflate=True, garbage=3)
    ndoc.close(); sdoc.close()
    return {"pages": params}


def _job(a):
    src, dst, level, seed = a
    info = make_scan(Path(src), Path(dst), level, seed)
    return {"source": str(Path(src).relative_to(SYN)), "scan": str(Path(dst).relative_to(SYN)), "level": level, "seed": seed,
            "sha256": hashlib.sha256(Path(dst).read_bytes()).hexdigest(), **info}


def select_apps() -> list[str]:
    labels = json.loads((SYN / "labels.json").read_text())["applications"]
    rng = random.Random(SEED)
    chosen = []
    for split in ("dev", "test"):
        ids = sorted(a["application_id"] for a in labels if a["split"] == split)
        chosen += sorted(rng.sample(ids, N_PER_SPLIT))
    return chosen


def main() -> None:
    apps = select_apps()
    jobs = []
    for level in LEVELS:
        for app in apps:
            for src in sorted((SYN / "applications" / app).glob("upload_*.pdf")):
                seed = int(hashlib.sha256(f"{SEED}|{level}|{app}|{src.name}".encode()).hexdigest()[:8], 16)
                jobs.append((str(src), str(OUT / f"L{level}" / app / src.name), level, seed))
    with ProcessPoolExecutor(max_workers=4) as ex:
        items = list(ex.map(_job, jobs))
    (OUT / "manifest.json").write_text(json.dumps({"note": "Synthetic scans (MOCK). Generated from digital synthetic PDFs.", "seed": SEED, "dpi": DPI,
                                                   "levels": {str(k): v for k, v in LEVELS.items()}, "applications": apps, "items": items}, indent=1))
    print(f"{len(items)} scans for {len(apps)} applications")


if __name__ == "__main__":
    main()
