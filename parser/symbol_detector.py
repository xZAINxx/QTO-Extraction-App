"""Bridge between :mod:`cv.yolo_inference` and the assembler.

The assembler calls :func:`detect_symbols_in_zone` once per page when
``zones.plan_body`` is non-empty and the page type is plan/elevation.
The output is a list of dicts shaped like the legend/schedule extractors
so :class:`core.assembler.Assembler._make_row` can consume them
directly — one row per detected class with ``qty = count``.

Vision counting is **opt-in**: if either ultralytics or the weights file
is missing, this module degrades to "do nothing" silently. That keeps
the Phase 1 cost gate green even on machines without torch.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import fitz
import numpy as np

from parser.zone_segmenter import SheetZones, Zone


_LOG = logging.getLogger(__name__)
_DEFAULT_DPI = 200


# Map raw class names from the FloorPlanCAD-pretrained model to QTO units.
# "EA" for items that are inherently countable; the operator can override
# in the assembly editor.
_UNIT_BY_CLASS: dict[str, str] = {
    "door":     "EA",
    "window":   "EA",
    "drain":    "EA",
    "light":    "EA",
    "fixture":  "EA",
    "outlet":   "EA",
    "diffuser": "EA",
    "sprinkler": "EA",
}

# Human-readable description seed; description_composer can refine later.
_DESC_BY_CLASS: dict[str, str] = {
    "door":     "DOOR (counted from plan)",
    "window":   "WINDOW (counted from plan)",
    "drain":    "ROOF/FLOOR DRAIN (counted from plan)",
    "light":    "LIGHT FIXTURE (counted from plan)",
    "fixture":  "PLUMBING FIXTURE (counted from plan)",
    "outlet":   "ELECTRICAL OUTLET (counted from plan)",
    "diffuser": "AIR DIFFUSER (counted from plan)",
    "sprinkler": "SPRINKLER HEAD (counted from plan)",
}


@dataclass
class SymbolCount:
    """One aggregate row destined for the assembler."""
    class_name: str
    qty: int
    units: str
    description: str
    bboxes: list[tuple[float, float, float, float]]  # in zone-local pixel coords


def _zone_pixels(page: fitz.Page, zone: Zone, dpi: int) -> np.ndarray:
    """Render the plan-body zone to an RGB pixel array."""
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=matrix, clip=zone.rect, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[..., :3]
    elif pix.n == 1:
        img = np.repeat(img, 3, axis=2)
    return img


def detect_symbols_in_zone(
    page: fitz.Page,
    zones: SheetZones,
    *,
    weights_path: Optional[Path] = None,
    classes: Optional[Iterable[str]] = None,
    dpi: int = _DEFAULT_DPI,
    conf_threshold: float = 0.35,
) -> list[SymbolCount]:
    """Detect countable symbols across every plan-body zone on the page.

    Returns an empty list when CV is unavailable so callers don't need
    to special-case the missing-deps path.
    """
    if not zones.plan_bodies:
        return []

    try:
        from cv.yolo_inference import YOLODetector, DEFAULT_WEIGHTS  # local: heavy imports
    except Exception as e:
        _LOG.debug("symbol detection skipped (ultralytics unavailable): %s", e)
        return []

    weights = Path(weights_path or DEFAULT_WEIGHTS)
    if not weights.exists():
        _LOG.info("symbol detection skipped (weights missing at %s)", weights)
        return []

    try:
        detector = YOLODetector.get(weights)
    except Exception as e:
        _LOG.warning("YOLO model failed to load: %s", e)
        return []

    aggregate: dict[str, list[tuple[float, float, float, float]]] = {}
    for zone in zones.plan_bodies:
        img = _zone_pixels(page, zone, dpi)
        try:
            result = detector.infer(
                img,
                conf_threshold=conf_threshold,
                classes=classes,
            )
        except Exception as e:
            _LOG.warning("YOLO inference failed on zone %s: %s", zone.label, e)
            continue
        for det in result.detections:
            aggregate.setdefault(det.class_name, []).append(
                (det.x0, det.y0, det.x1, det.y1)
            )

    out: list[SymbolCount] = []
    for cls_name, bboxes in aggregate.items():
        out.append(SymbolCount(
            class_name=cls_name,
            qty=len(bboxes),
            units=_UNIT_BY_CLASS.get(cls_name, "EA"),
            description=_DESC_BY_CLASS.get(cls_name, f"{cls_name.upper()} (counted from plan)"),
            bboxes=bboxes,
        ))
    return out


def to_qto_items(counts: list[SymbolCount], sheet: str) -> list[dict]:
    """Adapt :class:`SymbolCount` records into the dict shape :class:`Assembler._make_row` expects."""
    items: list[dict] = []
    for c in counts:
        items.append({
            "description": c.description,
            "qty": c.qty,
            "units": c.units,
            "details_override": f"PLAN/{sheet.replace('-', '')}" if sheet else "PLAN",
            "category_label": "COUNT",
        })
    return items
