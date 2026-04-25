"""Drawing-zone segmenter — Togal-style page partitioning before any AI call.

Given a single PDF page (PyMuPDF), segment it into rectangular regions
(title-block, legend, schedule, plan-body, notes) using only OpenCV +
heuristics. Cropping each region tightly before sending to vision models
slashes token cost ~5-10x compared to full-page calls.

Algorithm
---------
1. Render the page at 150 DPI (rotation-aware, so output is always upright).
2. Binarise via adaptive threshold; morphologically open with horizontal +
   vertical kernels to extract long lines.
3. Take the union of horizontal/vertical line masks → grid mask.
4. Connected-component analysis on the inverted grid → candidate boxes.
5. Score each candidate against zone heuristics (position, aspect ratio,
   density, vertical-line count) → assign a zone label.
6. Always carve a right-side strip as `title_block` whether or not the
   morphology found a clean rectangle there (Brooklyn drawings have
   rotated text spanning full strip without grid lines).

Output rectangles are in PAGE-SPACE coordinates (PyMuPDF ``Rect`` units),
so callers can crop the page directly without rescaling.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Iterable, Optional

import fitz
import numpy as np

try:
    import cv2  # type: ignore
    _HAS_CV2 = True
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore
    _HAS_CV2 = False


_RENDER_DPI = 150


@dataclass
class Zone:
    rect: fitz.Rect
    label: str          # "title_block" | "legend" | "schedule" | "plan_body" | "notes"
    score: float = 0.0
    grid_density: float = 0.0   # 0-1 fraction of pixels on grid lines


@dataclass
class SheetZones:
    page_num: int
    page_rect: fitz.Rect
    title_block: Optional[Zone] = None
    legends: list[Zone] = field(default_factory=list)
    schedules: list[Zone] = field(default_factory=list)
    plan_bodies: list[Zone] = field(default_factory=list)
    notes: list[Zone] = field(default_factory=list)
    rotation: int = 0

    @property
    def all_zones(self) -> list[Zone]:
        zs: list[Zone] = []
        if self.title_block:
            zs.append(self.title_block)
        zs.extend(self.legends)
        zs.extend(self.schedules)
        zs.extend(self.plan_bodies)
        zs.extend(self.notes)
        return zs


# ── Public API ─────────────────────────────────────────────────────────────


def segment(page: fitz.Page, page_num: Optional[int] = None) -> SheetZones:
    """Segment a single PDF page into typed rectangles."""
    if page_num is None:
        page_num = page.number + 1
    page_rect = page.rect
    rotation = page.rotation

    zones = SheetZones(page_num=page_num, page_rect=page_rect, rotation=rotation)

    # Always carve a right-side strip for the title block. PDFs from SCA / NYCSCA
    # routinely have the title block in a rotated 1.5-inch strip on the right
    # edge — sometimes graphic-only, often without a clean grid.
    tb_rect = _right_strip(page_rect, pct=0.18)
    zones.title_block = Zone(rect=tb_rect, label="title_block", score=1.0)

    if not _HAS_CV2:
        # Fallback when OpenCV is unavailable: emit the un-stripped body
        # as a single plan_body zone so downstream still works.
        body = fitz.Rect(page_rect.x0, page_rect.y0, tb_rect.x0, page_rect.y1)
        zones.plan_bodies.append(Zone(rect=body, label="plan_body", score=0.5))
        return zones

    img = _render_page(page)            # ndarray HxWx3 uint8
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    bin_inv = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV, blockSize=25, C=10,
    )

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 60), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, h // 60)))
    h_lines = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, h_kernel, iterations=1)
    v_lines = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, v_kernel, iterations=1)
    grid = cv2.bitwise_or(h_lines, v_lines)

    # Dilate grid so close lines join into rectangles, then find external CCs.
    grid_d = cv2.dilate(grid, np.ones((3, 3), np.uint8), iterations=2)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grid_d, connectivity=8)

    page_w_pt = page_rect.width
    page_h_pt = page_rect.height
    sx = page_w_pt / w
    sy = page_h_pt / h
    tb_x_pt = tb_rect.x0          # exclude anything that overlaps title-block strip

    candidates: list[Zone] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw < w * 0.08 or bh < h * 0.04:
            continue
        if area < (w * h) * 0.003:
            continue
        # Reject the giant near-page-bounding component (border)
        if bw > w * 0.95 and bh > h * 0.95:
            continue

        rect_px = (x, y, x + bw, y + bh)
        rx0, ry0 = x * sx, y * sy
        rx1, ry1 = (x + bw) * sx, (y + bh) * sy
        # Skip anything fully in the title-block strip (it'll be merged in).
        if rx0 >= tb_x_pt - 1:
            continue
        # Clip to body region (left of title-block strip).
        rx1 = min(rx1, tb_x_pt)
        if rx1 - rx0 < page_w_pt * 0.08:
            continue

        density = float(np.mean(grid[y:y+bh, x:x+bw] > 0))
        v_density = float(np.mean(v_lines[y:y+bh, x:x+bw] > 0))
        h_density = float(np.mean(h_lines[y:y+bh, x:x+bw] > 0))
        aspect = bw / max(bh, 1)
        position_x = (x + bw / 2) / w   # 0=left, 1=right
        position_y = (y + bh / 2) / h   # 0=top, 1=bottom

        label = _classify_zone(
            position_x, position_y, aspect, density, v_density, h_density,
        )
        candidates.append(
            Zone(
                rect=fitz.Rect(rx0, ry0, rx1, ry1),
                label=label,
                score=density,
                grid_density=density,
            )
        )

    # Merge overlapping same-label rectangles.
    merged = _merge_overlapping(candidates)

    # Emit zones into the appropriate buckets, dropping anything that fully
    # overlaps a stronger (denser) zone of the same type.
    for z in merged:
        if z.label == "legend":
            zones.legends.append(z)
        elif z.label == "schedule":
            zones.schedules.append(z)
        elif z.label == "notes":
            zones.notes.append(z)
        else:
            zones.plan_bodies.append(z)

    # If nothing was found in the body, treat the whole left-of-titleblock as
    # a single plan_body so vision fallbacks still have something to crop.
    if not zones.plan_bodies and not zones.legends and not zones.schedules:
        body = fitz.Rect(page_rect.x0, page_rect.y0, tb_x_pt, page_rect.y1)
        zones.plan_bodies.append(Zone(rect=body, label="plan_body", score=0.3))

    return zones


# ── Helpers ────────────────────────────────────────────────────────────────


def _right_strip(rect: fitz.Rect, pct: float = 0.18) -> fitz.Rect:
    w = rect.width
    return fitz.Rect(rect.x0 + w * (1 - pct), rect.y0, rect.x1, rect.y1)


def _render_page(page: fitz.Page, dpi: int = _RENDER_DPI) -> np.ndarray:
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pm = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, pm.n)
    if pm.n == 4:  # RGBA → drop alpha
        img = img[..., :3]
    elif pm.n == 1:
        img = np.repeat(img, 3, axis=2)
    return img


def _classify_zone(
    px: float, py: float, aspect: float,
    density: float, v_density: float, h_density: float,
) -> str:
    """Heuristic mapping from box features to zone label.

    Tunables intentionally permissive so the assembler can re-confirm the
    label via downstream extractors before incurring vision cost.
    """
    # Schedules: tall multi-column tables (high vertical-line density).
    if v_density > 0.04 and aspect < 2.5:
        return "schedule"
    # Legends: small / medium rectangles, usually upper-right of the body.
    if density > 0.02 and aspect < 4.0 and (px > 0.55 or py < 0.35):
        return "legend"
    # Notes / general-notes columns: tall narrow text columns.
    if aspect < 0.6:
        return "notes"
    # Default: drawing body.
    return "plan_body"


def _merge_overlapping(zones: list[Zone]) -> list[Zone]:
    """Greedy union of same-label rectangles whose IoU > 0.2."""
    if not zones:
        return []
    out: list[Zone] = []
    used = [False] * len(zones)
    for i in range(len(zones)):
        if used[i]:
            continue
        cur = zones[i]
        for j in range(i + 1, len(zones)):
            if used[j] or zones[j].label != cur.label:
                continue
            if _iou(cur.rect, zones[j].rect) > 0.2:
                cur = Zone(
                    rect=cur.rect | zones[j].rect,
                    label=cur.label,
                    score=max(cur.score, zones[j].score),
                    grid_density=max(cur.grid_density, zones[j].grid_density),
                )
                used[j] = True
        used[i] = True
        out.append(cur)
    return out


def _iou(a: fitz.Rect, b: fitz.Rect) -> float:
    inter = a & b
    if inter.is_empty:
        return 0.0
    union_area = a.get_area() + b.get_area() - inter.get_area()
    return inter.get_area() / union_area if union_area > 0 else 0.0


def crop_zone_png(page: fitz.Page, zone: Zone, dpi: int = 150) -> bytes:
    """Render the given zone rectangle as PNG bytes."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pm = page.get_pixmap(matrix=mat, clip=zone.rect, alpha=False)
    return pm.tobytes("png")
