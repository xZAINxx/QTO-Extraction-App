"""Schedule-table extractor — runs after zone segmentation.

For each `zones.schedules` rectangle:
1. Try `pdfplumber.Page.crop(bbox).extract_tables()` first (free; vector-only).
2. If pdfplumber returns < 2 rows or only blank rows, fall back to a Sonnet
   vision call cropped to the same rectangle at 150 DPI.

Output schema is identical to legacy `extract_type_c` so downstream
assembler code keeps working unchanged: ``[{"id", "description", "qty"}]``.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

import fitz

from parser.zone_segmenter import SheetZones, Zone, crop_zone_png


_SCHEDULE_PROMPT = (
    "Extract every row from this construction SCHEDULE table on an "
    "architectural drawing (e.g. door schedule, room schedule, finish "
    "schedule, equipment schedule). "
    "Return ONLY a JSON array (no markdown fences, no preamble) of objects "
    'with this schema: [{"id": string, "description": string, "qty": number}]. '
    "Rules:\n"
    "- id: the mark/tag/room-number from the leftmost column.\n"
    "- description: concatenated row description, joining notable columns "
    "(type, size, material, finish) with ' / '.\n"
    "- qty: numeric column if present (count, area), else 1.\n"
    "Skip header rows, repeated headers across multi-page schedules, and "
    "blank/divider rows."
)


def extract_schedules(
    page: fitz.Page,
    zones: SheetZones,
    pdf_path: str,
    ai_client=None,
) -> list[dict]:
    """Return one flat list of schedule rows across every schedule zone."""
    if not zones.schedules:
        return []

    out: list[dict] = []
    for z in zones.schedules:
        rows = _try_pdfplumber(pdf_path, page.number, z.rect)
        if rows and _looks_useful(rows):
            out.extend(_normalize_rows(rows))
            continue
        if ai_client is not None:
            try:
                img = crop_zone_png(page, z, dpi=150)
                raw = ai_client.extract_schedule_from_image(img, _SCHEDULE_PROMPT)
                items = json.loads(_strip_fences(raw))
                if isinstance(items, list):
                    out.extend(_clean_vision(items))
            except Exception:
                pass
    return out


# ── pdfplumber path ────────────────────────────────────────────────────────


def _try_pdfplumber(pdf_path: str, page_idx: int, rect: fitz.Rect) -> list[list[str]]:
    try:
        import pdfplumber
    except Exception:
        return []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_idx]
            # pdfplumber uses unrotated PDF coords; safe-clip and extract.
            try:
                bbox = (rect.x0, rect.y0, rect.x1, rect.y1)
                cropped = page.crop(bbox, relative=False)
            except Exception:
                cropped = page
            tables = cropped.extract_tables() or []
            rows: list[list[str]] = []
            for tbl in tables:
                for row in tbl:
                    rows.append([("" if c is None else str(c).strip()) for c in row])
            return rows
    except Exception:
        return []


def _looks_useful(rows: list[list[str]]) -> bool:
    non_empty = [r for r in rows if any(c.strip() for c in r)]
    if len(non_empty) < 2:
        return False
    cols = max((len(r) for r in non_empty), default=0)
    return cols >= 2


def _normalize_rows(rows: list[list[str]]) -> list[dict]:
    if not rows:
        return []
    header = [c.upper() for c in rows[0]]
    desc_idx = _find_col(header, ["DESCRIPTION", "TYPE", "SIZE", "ROOM",
                                  "MATERIAL", "FINISH", "ITEM"])
    qty_idx = _find_col(header, ["QTY", "COUNT", "QUANTITY", "NO.", "AREA"])
    mark_idx = _find_col(header, ["MARK", "TAG", "ID", "ROOM #", "NO"])

    out: list[dict] = []
    for row in rows[1:]:
        if not any(c for c in row):
            continue
        cells = [c for c in row]
        desc = (
            cells[desc_idx] if desc_idx is not None and desc_idx < len(cells)
            else " / ".join(c for c in cells if c)
        )
        qty_raw = (
            cells[qty_idx] if qty_idx is not None and qty_idx < len(cells)
            else "1"
        )
        mark = (
            cells[mark_idx] if mark_idx is not None and mark_idx < len(cells)
            else ""
        )
        qty = _parse_qty(qty_raw)
        if desc:
            out.append({"id": mark, "description": desc, "qty": qty})
    return out


def _clean_vision(items: list) -> list[dict]:
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        desc = str(it.get("description", "")).strip()
        if not desc:
            continue
        try:
            qty = float(it.get("qty", 1) or 1)
        except Exception:
            qty = 1.0
        out.append({
            "id": str(it.get("id", "")).strip(),
            "description": desc,
            "qty": qty,
        })
    return out


def _find_col(header: list[str], candidates: list[str]) -> Optional[int]:
    for cand in candidates:
        for i, h in enumerate(header):
            if cand in h:
                return i
    return None


def _parse_qty(raw: str) -> float:
    m = re.search(r'[\d,]+(?:\.\d+)?', (raw or "").replace(",", ""))
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            pass
    return 1.0


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        parts = s.split("```", 2)
        if len(parts) >= 2:
            inner = parts[1]
            if inner.lower().startswith("json"):
                inner = inner[4:]
            return inner.strip()
    return s
