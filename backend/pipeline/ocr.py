"""RapidOCR wrapper. Returns word-level boxes (from per-character boxes) with confidence, in image pixels."""
from __future__ import annotations

import threading

import numpy as np

GAP_FRAC = 0.2  # minimum word-break gap as a fraction of line height; DEV-tuned
GAP_MULT = 2.0  # word-break gap as a multiple of the line's median intra-word gap; DEV-tuned
_engine = None
_lock = threading.RLock()


def engine():
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
        return _engine


def _box(pts) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _ink_runs(gray: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> list[tuple[int, int]]:
    """Whitespace runs (start, end) in absolute x between ink columns of a line crop, found from the pixels rather than the recogniser."""
    h, w = gray.shape
    x0, x1, y0, y1 = max(0, x0), min(w, x1), max(0, y0), min(h, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    crop = gray[y0:y1, x0:x1]
    thr = max(10.0, float(np.percentile(crop, 50)) - 0.45 * (float(np.percentile(crop, 50)) - float(np.percentile(crop, 1))))
    ink = (crop < thr).sum(axis=0) >= 1
    runs, start = [], None
    xs = np.flatnonzero(ink)
    if xs.size == 0:
        return []
    for a, b in zip(xs[:-1], xs[1:]):
        if b - a > 1:
            runs.append((x0 + int(a) + 1, x0 + int(b)))
    return runs


def ocr_words(gray: np.ndarray) -> list[tuple]:
    """[(x0, y0, x1, y1, text, conf)] in pixels. Words come from RapidOCR's per-character boxes split on spaces."""
    with _lock:
        res, _ = engine()(gray, return_word_box=True)
    words: list[tuple] = []
    for item in res or []:
        line_box, line_text, line_conf = item[0], item[1], float(item[2])
        if len(item) >= 6 and item[3] and len(item[3]) == len(item[4]):
            cur: list[int] = []
            boxes = [_box(b) for b in item[3]]
            lh = max(1.0, float(np.median([b[3] - b[1] for b in boxes])))
            xs0, xs1 = int(min(b[0] for b in boxes)), int(max(b[2] for b in boxes))
            ys0, ys1 = int(min(b[1] for b in boxes)), int(max(b[3] for b in boxes))
            runs = _ink_runs(gray, xs0, xs1, ys0 + int(0.2 * lh), ys1 - int(0.2 * lh))
            lens = [b - a for a, b in runs]
            thr = max(GAP_FRAC * lh, GAP_MULT * float(np.median(lens))) if lens else GAP_FRAC * lh
            breaks = [(a, b) for a, b in runs if b - a > thr]

            cx = lambda b: (b[0] + b[2]) / 2  # noqa: E731

            def flush():
                if cur:
                    bs = [boxes[i] for i in cur]
                    words.append((min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs),
                                  "".join(item[4][i] for i in cur), float(np.mean([item[5][i] for i in cur]))))
                    cur.clear()
            for i, ch in enumerate(item[4]):
                if ch.strip() == "":
                    flush()
                    continue
                # the recogniser often drops space characters: also break where the horizontal gap between glyph boxes is word-sized
                if cur and any(cx(boxes[cur[-1]]) < (a + b) / 2 <= cx(boxes[i]) for a, b in breaks):
                    flush()
                cur.append(i)
            flush()
        else:  # fallback: split the line box proportionally by character count
            x0, y0, x1, y1 = _box(line_box)
            toks = line_text.split()
            total = sum(len(t) for t in toks) + max(0, len(toks) - 1)
            x = x0
            for t in toks:
                w = (x1 - x0) * len(t) / max(1, total)
                words.append((x, y0, x + w, y1, t, line_conf))
                x += w + (x1 - x0) / max(1, total)
    return words
