"""ALLOWANCES & PROVISIONS extractor for T-002 cover sheets.

Tightened semantics (Phase 1 Step 6):
- Each row is parsed verbatim; the section label "ALLOWANCE" or "PROVISION"
  becomes a mandatory description prefix.
- detail_refs are always emitted as ``ALLOWANCES# {n}/T002`` or
  ``PROVISIONS# {n}/T002`` (the format the GC estimate format expects).
- Inline qty hints (``"5 LS"``, ``"2 EA"``) are parsed off the description so
  the row carries an accurate quantity instead of always-1.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import fitz


_INLINE_QTY_RE = re.compile(
    r'\b(\d+(?:\.\d+)?)\s*(LS|EA|LF|SF|SQ\s*FT|SQFT|CY|YARD|YDS?|HRS?)\b',
    re.IGNORECASE,
)


def extract_allowances(page: fitz.Page, sheet_info, ai_client=None) -> list[dict]:
    items = _try_pdfplumber(page)
    if len(items) < 2 and ai_client is not None:
        v_items = _try_vision(page, ai_client)
        # Use vision result only if it found more rows than pdfplumber.
        if len(v_items) > len(items):
            items = v_items
    return items


# ── pdfplumber path ────────────────────────────────────────────────────────


def _try_pdfplumber(page: fitz.Page) -> list[dict]:
    try:
        import pdfplumber
    except Exception:
        return []
    try:
        pdf_path = page.parent.name
        page_num = page.number
        with pdfplumber.open(pdf_path) as pdf:
            pl_page = pdf.pages[page_num]
            tables = pl_page.extract_tables() or []
    except Exception:
        return []

    items: list[dict] = []
    for table in tables:
        section: Optional[str] = None
        for row in table:
            if not row:
                continue
            cells = [str(c).strip() if c else "" for c in row]
            row_text = " ".join(c for c in cells if c).upper()
            # Section header rows: "ALLOWANCES", "PROVISIONS", "PROVISION ITEMS"
            if "ALLOWANCE" in row_text and _is_header_row(cells):
                section = "ALLOWANCE"
                continue
            if "PROVISION" in row_text and _is_header_row(cells):
                section = "PROVISION"
                continue
            if section is None:
                continue
            parsed = _row_to_item(cells, section)
            if parsed:
                items.append(parsed)
    return items


def _is_header_row(cells: list[str]) -> bool:
    """True iff every column past the first is empty (so the row is a banner)."""
    return not any(c for c in cells[1:] if c)


def _row_to_item(cells: list[str], section: str) -> Optional[dict]:
    if not cells or not any(cells):
        return None
    num = cells[0]
    desc = " ".join(c for c in cells[1:] if c) or (cells[0] if len(cells) == 1 else "")
    if not desc:
        return None
    qty, units, desc_clean = _parse_inline_qty(desc)
    ref_type = "ALLOWANCES" if section == "ALLOWANCE" else "PROVISIONS"
    return {
        "description": f"({section}) {desc_clean.upper()}",
        "detail_refs": [f"{ref_type}# {num}/T002"] if num else [],
        "units": units,
        "qty": qty,
    }


def _parse_inline_qty(text: str) -> tuple[float, str, str]:
    """Return (qty, units, description-with-qty-stripped)."""
    m = _INLINE_QTY_RE.search(text)
    if not m:
        return 1.0, "LS", text
    qty = float(m.group(1))
    units = m.group(2).upper().replace(" ", "")
    if units in ("SQFT", "SQFT", "SQ FT"):
        units = "SF"
    elif units in ("YARD", "YARDS", "YDS", "YD"):
        units = "CY"
    cleaned = (text[: m.start()] + text[m.end():]).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(" ,;")
    return qty, units, cleaned or text


# ── Vision fallback ────────────────────────────────────────────────────────


def _try_vision(page: fitz.Page, ai_client) -> list[dict]:
    try:
        from parser.pdf_splitter import get_page_image
        img_bytes = get_page_image(page, dpi=150)
        prompt = (
            "This is a T-002 ALLOWANCES/PROVISIONS sheet from construction drawings. "
            "Extract every numbered row from BOTH the ALLOWANCES and PROVISIONS tables. "
            "Return ONLY a JSON array (no markdown fences) of objects with schema: "
            '[{"number": integer, "section": "ALLOWANCE"|"PROVISION", '
            '"description": string, "qty": number|null, "units": string}]. '
            "Default qty=1 and units='LS' if not visible."
        )
        raw = ai_client.extract_legend_from_image(img_bytes, prompt)
        items_raw = json.loads(_strip_fences(raw))
    except Exception:
        return []

    items: list[dict] = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        section = str(item.get("section", "ALLOWANCE")).upper()
        desc = str(item.get("description", "")).upper().strip()
        if not desc:
            continue
        num = item.get("number")
        try:
            qty = float(item.get("qty") or 1)
        except Exception:
            qty = 1.0
        units = str(item.get("units") or "LS").upper().strip() or "LS"
        ref_type = "ALLOWANCES" if "ALLOWANCE" in section else "PROVISIONS"
        items.append({
            "description": f"({section}) {desc}",
            "detail_refs": [f"{ref_type}# {num}/T002"] if num else [],
            "units": units,
            "qty": qty,
        })
    return items


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
