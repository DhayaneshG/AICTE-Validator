"""Routes pages without a text layer to preprocessing + OCR and rebuilds the page structure (lines/cells with bbox, in PDF points)."""
from __future__ import annotations

import os

from . import preprocess
from .extract import render_page_array, words_to_page
from .ocr import ocr_words

OCR_DPI = int(os.environ.get("AICTE_OCR_DPI", 200))


def route(data: bytes, ex: dict) -> tuple[dict, str]:
    """Replace each text-less page by an OCR'd page. Returns (extraction, source) where source is 'ocr' or 'mixed'."""
    scale = 72.0 / OCR_DPI
    routed = 0
    for i, p in enumerate(ex["pages"]):
        if p["has_text"]:
            continue
        gray = render_page_array(data, p["page"], OCR_DPI)
        clean, info = preprocess.preprocess(gray)
        words = ocr_words(clean)
        pts = [(w[0] * scale, w[1] * scale, w[2] * scale, w[3] * scale, w[4], w[5]) for w in words]
        np_ = words_to_page(pts, p["page"], p["width"], p["height"], source="ocr")
        np_.update({"has_text": False, "ocr": True, "n_images": p.get("n_images", 0), "preprocess": info,
                    "mean_word_conf": round(sum(w[5] for w in words) / len(words), 4) if words else 0.0, "n_words": len(words)})
        ex["pages"][i] = np_
        routed += 1
    ex["ocr_pages"] = routed
    ex["has_text_layer"] = routed == 0
    ex["any_text_layer"] = routed < len(ex["pages"])
    return ex, ("ocr" if routed == len(ex["pages"]) else "mixed_ocr")


def register(pipeline) -> None:
    pipeline.ocr_router = route
