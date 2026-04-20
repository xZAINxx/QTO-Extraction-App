"""Extract geometry-based quantities from plan pages using pymupdf path objects."""
import math
import statistics
from typing import Optional

import fitz


def read_geometry(page: fitz.Page, scale_pdf_per_foot: Optional[float]) -> dict:
    """
    Extract geometry quantities from the page.
    Returns: {areas_sf: float, wall_lengths_lf: float, door_window_count: int}
    """
    if scale_pdf_per_foot is None:
        return {"areas_sf": 0.0, "wall_lengths_lf": 0.0, "door_window_count": 0}

    paths = page.get_drawings()
    if not paths:
        return {"areas_sf": 0.0, "wall_lengths_lf": 0.0, "door_window_count": 0}

    areas_sf = _measure_closed_areas(paths, scale_pdf_per_foot)
    wall_lf = _measure_wall_lengths(paths, scale_pdf_per_foot)
    door_count = _count_door_window_blocks(paths, scale_pdf_per_foot)

    return {
        "areas_sf": round(areas_sf, 1),
        "wall_lengths_lf": round(wall_lf, 1),
        "door_window_count": door_count,
    }


def _measure_closed_areas(paths: list, scale: float) -> float:
    """Sum areas of closed polylines with ≥4 vertices."""
    total_area_pts = 0.0
    for path in paths:
        if not path.get("closePath"):
            continue
        items = path.get("items", [])
        pts = []
        for item in items:
            kind = item[0]
            if kind == "l":
                pts.append(item[2])   # line: (type, p1, p2) → p2
            elif kind == "m":
                pts.append(item[1])   # moveto
        if len(pts) >= 4:
            area = _shoelace(pts)
            total_area_pts += area

    # Convert PDF points² → feet²
    # scale = PDF_pts per foot → scale² = PDF_pts² per foot²
    if scale > 0:
        return total_area_pts / (scale * scale)
    return 0.0


def _measure_wall_lengths(paths: list, scale: float) -> float:
    """Sum lengths of heavy stroke lines (stroke-width above median)."""
    if not paths:
        return 0.0

    widths = [p.get("width", 0) for p in paths if p.get("width", 0) > 0]
    if not widths:
        return 0.0
    median_w = statistics.median(widths)
    threshold = median_w * 1.5

    total_pts = 0.0
    for path in paths:
        if path.get("width", 0) < threshold:
            continue
        items = path.get("items", [])
        prev = None
        for item in items:
            kind = item[0]
            if kind == "m":
                prev = item[1]
            elif kind == "l" and prev:
                p2 = item[2]
                total_pts += _dist(prev, p2)
                prev = p2

    return total_pts / scale if scale > 0 else 0.0


def _count_door_window_blocks(paths: list, scale: float) -> int:
    """Count rectangular blocks at wall openings (small consistent rectangles on wall lines)."""
    rects = []
    for path in paths:
        if not path.get("closePath"):
            continue
        r = path.get("rect")
        if r:
            w = r.width
            h = r.height
            # Small rectangles: roughly door/window size in PDF units
            if scale > 0:
                w_ft = w / scale
                h_ft = h / scale
                if 0.2 < w_ft < 8.0 and 0.1 < h_ft < 4.0:
                    rects.append(path)
    return len(rects)


def _shoelace(pts: list) -> float:
    """Shoelace formula for polygon area (in PDF coordinate units²)."""
    n = len(pts)
    area = 0.0
    for i in range(n):
        x0, y0 = pts[i].x, pts[i].y
        x1, y1 = pts[(i + 1) % n].x, pts[(i + 1) % n].y
        area += x0 * y1 - x1 * y0
    return abs(area) / 2.0


def _dist(p1, p2) -> float:
    return math.sqrt((p2.x - p1.x) ** 2 + (p2.y - p1.y) ** 2)
