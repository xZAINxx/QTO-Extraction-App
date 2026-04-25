"""Multi-scale template matching for the Pattern Search workflow (Phase 4).

Architectural symbols (door swings, window tags, light fixtures, drains)
recur across many sheets at slightly different scales. ``cv2.matchTemplate``
with ``TM_CCOEFF_NORMED`` is robust to brightness shifts and fast enough
to scan a full plan body in <1 s per scale.

The user draws a bounding box around one example in the embedded PDF
viewer; ``match_multiscale`` returns every additional location that
matches at any of three scale factors (0.85, 1.0, 1.15), then NMS dedupes
overlapping detections.

Pure OpenCV — no torch, no per-call API cost.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from cv.patch_utils import nms


DEFAULT_SCALES: tuple[float, ...] = (0.85, 1.0, 1.15)
DEFAULT_THRESHOLD: float = 0.78  # TM_CCOEFF_NORMED is in [-1, 1]; 0.78 is "close visual match".


@dataclass(frozen=True)
class TemplateMatch:
    """One matched instance of a template."""
    x0: int
    y0: int
    x1: int
    y1: int
    score: float
    scale: float


def _to_grayscale(image: np.ndarray) -> np.ndarray:
    """Coerce HW or HWC arrays into single-channel grayscale uint8."""
    import cv2  # local import — keeps cold start fast for non-CV callers
    if image.ndim == 2:
        gray = image
    elif image.shape[2] == 4:
        gray = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    elif image.shape[2] == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        raise ValueError(f"unsupported image shape {image.shape}")
    if gray.dtype != np.uint8:
        gray = gray.astype(np.uint8, copy=False)
    return gray


def match_template(
    image: np.ndarray,
    template: np.ndarray,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[TemplateMatch]:
    """Single-scale match. Used internally by ``match_multiscale``.

    Skips silently (returns []) if the template is larger than the image
    so callers can sweep scales without bounds-checking each one.
    """
    import cv2
    img = _to_grayscale(image)
    tpl = _to_grayscale(template)
    th, tw = tpl.shape[:2]
    ih, iw = img.shape[:2]
    if th < 3 or tw < 3 or th > ih or tw > iw:
        return []

    result = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(result >= threshold)
    matches: list[TemplateMatch] = []
    for x, y in zip(xs.tolist(), ys.tolist()):
        score = float(result[y, x])
        matches.append(TemplateMatch(
            x0=int(x), y0=int(y),
            x1=int(x + tw), y1=int(y + th),
            score=score,
            scale=1.0,
        ))
    return matches


def match_multiscale(
    image: np.ndarray,
    template: np.ndarray,
    scales: Sequence[float] = DEFAULT_SCALES,
    threshold: float = DEFAULT_THRESHOLD,
    iou_threshold: float = 0.3,
    max_matches: Optional[int] = None,
) -> list[TemplateMatch]:
    """Run ``match_template`` across multiple scales and NMS-dedupe.

    Parameters
    ----------
    scales
        Multipliers applied to the **template**, not the image (faster).
    iou_threshold
        Two boxes overlapping by more than this are deduped.
    max_matches
        Optional cap; the highest-scoring matches survive.
    """
    import cv2
    th, tw = template.shape[:2]

    all_matches: list[TemplateMatch] = []
    for scale in scales:
        new_w = max(1, int(round(tw * scale)))
        new_h = max(1, int(round(th * scale)))
        if scale == 1.0:
            scaled = template
        else:
            interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            scaled = cv2.resize(template, (new_w, new_h), interpolation=interp)
        for m in match_template(image, scaled, threshold):
            all_matches.append(TemplateMatch(
                m.x0, m.y0, m.x1, m.y1, m.score, scale,
            ))

    if not all_matches:
        return []

    boxes = [(m.x0, m.y0, m.x1, m.y1) for m in all_matches]
    scores = [m.score for m in all_matches]
    keep = nms(boxes, scores, iou_threshold)
    out = [all_matches[i] for i in keep]
    out.sort(key=lambda m: m.score, reverse=True)
    if max_matches is not None:
        out = out[:max_matches]
    return out
