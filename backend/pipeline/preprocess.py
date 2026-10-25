"""Scan preprocessing: grayscale, deskew, light denoise, contrast normalisation. Deterministic (no randomness)."""
from __future__ import annotations

import cv2
import numpy as np


def estimate_skew(gray: np.ndarray, max_angle: float = 10.0) -> float:
    """Skew angle in degrees (positive = text rotated counter-clockwise) from the minimum-area rectangle of ink pixels,
    refined with a projection-profile search. Returns 0.0 when there is too little ink."""
    h, w = gray.shape
    scale = 800.0 / max(h, w)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    small = cv2.GaussianBlur(small, (3, 3), 0)
    bw = cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12)  # local threshold: robust to shadows/gradients
    if cv2.countNonZero(bw) < 200:
        return 0.0
    zero_score = float(np.var(bw.sum(axis=1).astype(np.float64)))
    best, best_score = 0.0, -1.0
    for a in np.arange(-max_angle, max_angle + 0.01, 0.5):
        M = cv2.getRotationMatrix2D((small.shape[1] / 2, small.shape[0] / 2), a, 1.0)
        r = cv2.warpAffine(bw, M, (small.shape[1], small.shape[0]), flags=cv2.INTER_NEAREST)
        prof = r.sum(axis=1).astype(np.float64)
        score = float(np.var(prof))
        if score > best_score:
            best, best_score = float(a), score
    if best_score < 1.03 * zero_score:  # no clear improvement over "not rotated": leave the page alone
        return 0.0
    return -best  # rotating the image by `best` straightens it; the skew is the opposite


def deskew(gray: np.ndarray, angle: float | None = None) -> tuple[np.ndarray, float]:
    a = estimate_skew(gray) if angle is None else angle
    if abs(a) < 0.3:
        return gray, 0.0
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), -a, 1.0)
    out = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return out, float(a)


def normalise(gray: np.ndarray) -> np.ndarray:
    """Remove slow illumination gradients (shadows) and stretch contrast."""
    bg = cv2.medianBlur(cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA), 21)
    bg = cv2.resize(bg, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    flat = cv2.divide(gray, np.maximum(bg, 1), scale=255)
    return cv2.normalize(flat, None, 0, 255, cv2.NORM_MINMAX)


def boost_faint(gray: np.ndarray, gamma: float = 2.0) -> np.ndarray:
    """Darken faint (pale grey) text relative to the paper tone so the detector can see small light labels."""
    paper = np.percentile(gray, 60)
    lo = np.percentile(gray, 0.5)
    x = np.clip((gray.astype(np.float32) - lo) / max(1.0, paper - lo), 0, 1)
    return (255 * x ** gamma).astype(np.uint8)


def preprocess(gray: np.ndarray) -> tuple[np.ndarray, dict]:
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    d, angle = deskew(gray)
    out = boost_faint(normalise(d))
    return out, {"deskew_deg": round(angle, 2), "steps": ["deskew", "flatten_illumination", "faint_text_boost"]}
