"""Image-tiling and detection-stitching helpers.

Architectural drawings are huge (often >5000 px on the long edge after a
high-DPI render), so YOLO and template matching both run on overlapping
640×640 patches. These utilities standardise:

* deterministic patch generation with overlap (avoids missing detections
  that straddle a tile edge);
* mapping a per-patch bbox back to the parent image's coordinate frame;
* class-aware non-maximum-suppression for stitching detections from
  neighbouring patches.

Pure-Python + NumPy; no torch / opencv heavy deps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

import numpy as np


PATCH_SIZE = 640
PATCH_OVERLAP = 64


@dataclass(frozen=True)
class Patch:
    """A single tile within a parent image.

    Attributes
    ----------
    x0, y0, x1, y1
        Patch bounds in the **parent image** coordinate frame, in pixels.
    image
        The cropped pixel array (H, W, 3) ready to feed to a model. May be
        smaller than ``PATCH_SIZE`` along right/bottom edges; callers that
        need a fixed input size must pad it themselves.
    """
    x0: int
    y0: int
    x1: int
    y1: int
    image: np.ndarray

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def iter_patches(
    image: np.ndarray,
    patch_size: int = PATCH_SIZE,
    overlap: int = PATCH_OVERLAP,
) -> Iterator[Patch]:
    """Yield overlapping patches that together cover ``image``.

    Step = ``patch_size - overlap``. The final row/column may be shorter
    than ``patch_size``; we never grow past the image bounds (callers
    pad if their model requires a fixed size).

    Raises
    ------
    ValueError
        If ``image`` is not a 3-D array or ``overlap`` ≥ ``patch_size``.
    """
    if image.ndim not in (2, 3):
        raise ValueError(f"image must be HW or HWC, got shape {image.shape}")
    if overlap >= patch_size:
        raise ValueError(f"overlap ({overlap}) must be < patch_size ({patch_size})")

    h, w = image.shape[:2]
    step = patch_size - overlap

    ys = list(range(0, max(1, h - overlap), step))
    xs = list(range(0, max(1, w - overlap), step))
    # Ensure the last patch always reaches the right/bottom edge.
    if ys[-1] + patch_size < h:
        ys.append(h - patch_size)
    if xs[-1] + patch_size < w:
        xs.append(w - patch_size)

    seen = set()
    for y in ys:
        y0 = max(0, y)
        y1 = min(h, y0 + patch_size)
        for x in xs:
            x0 = max(0, x)
            x1 = min(w, x0 + patch_size)
            if (x0, y0, x1, y1) in seen:
                continue
            seen.add((x0, y0, x1, y1))
            yield Patch(x0, y0, x1, y1, image[y0:y1, x0:x1])


def project_patch_box(patch: Patch, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Map a patch-local bbox into parent-image coordinates."""
    bx0, by0, bx1, by1 = box
    return (
        patch.x0 + bx0,
        patch.y0 + by0,
        patch.x0 + bx1,
        patch.y0 + by1,
    )


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter_w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    inter_h = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    area_a = max(0.0, (ax1 - ax0)) * max(0.0, (ay1 - ay0))
    area_b = max(0.0, (bx1 - bx0)) * max(0.0, (by1 - by0))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def nms(
    boxes: Sequence[tuple[float, float, float, float]],
    scores: Sequence[float],
    iou_threshold: float = 0.5,
) -> list[int]:
    """Greedy non-maximum-suppression. Returns the indices to keep.

    Pure NumPy / Python — no torch — so it works in environments where
    ultralytics isn't installed (e.g. unit tests).
    """
    if not boxes:
        return []
    order = sorted(range(len(boxes)), key=lambda i: scores[i], reverse=True)
    keep: list[int] = []
    suppressed = [False] * len(boxes)
    for i in order:
        if suppressed[i]:
            continue
        keep.append(i)
        for j in order:
            if j == i or suppressed[j]:
                continue
            if _iou(boxes[i], boxes[j]) >= iou_threshold:
                suppressed[j] = True
    return keep


def nms_per_class(
    boxes: Sequence[tuple[float, float, float, float]],
    scores: Sequence[float],
    classes: Sequence[str | int],
    iou_threshold: float = 0.5,
) -> list[int]:
    """Class-aware NMS: detections of different classes never suppress each other."""
    by_class: dict[str | int, list[int]] = {}
    for idx, cls in enumerate(classes):
        by_class.setdefault(cls, []).append(idx)

    keep: list[int] = []
    for cls, indices in by_class.items():
        sub_boxes = [boxes[i] for i in indices]
        sub_scores = [scores[i] for i in indices]
        kept_local = nms(sub_boxes, sub_scores, iou_threshold)
        keep.extend(indices[k] for k in kept_local)
    return sorted(keep)
