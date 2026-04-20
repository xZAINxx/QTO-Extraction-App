"""Extract sheet metadata from the title block (right-strip crop for H2M format)."""
import re
from dataclasses import dataclass
from typing import Optional

import fitz

from parser.pdf_splitter import crop_region_image


# Handles: A 100.00, PD 130.00, P 140 00, A-100, S5.1, etc.
_SHEET_NUM_RE = re.compile(
    r'^[A-Z]{1,3}\s*[-\s]?\s*\d{1,4}(?:[.\s]\d{1,2})?$',
    re.MULTILINE,
)
_DATE_RE = re.compile(
    r'\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{4}\b',
    re.IGNORECASE,
)


def normalize_sheet_number(sheet: str) -> str:
    """Convert A107.00 → A-107. Already-hyphenated formats (A-106, R-001, T-002) pass through unchanged."""
    sheet = sheet.strip()
    m = re.match(r'^([A-Za-z]{1,3})\s*(\d{1,4})(?:\.\d+)?$', sheet)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    return sheet


@dataclass
class TitleBlockInfo:
    sheet_number: str = ""
    sheet_title: str = ""
    project_name: str = ""
    contract: str = ""
    status: str = ""
    date: str = ""
    source: str = "vector"   # vector | vision


def read_title_block(
    page: fitz.Page,
    config: dict,
    ai_client=None,
) -> TitleBlockInfo:
    """Extract title block fields. Falls back to Claude Vision if vector text is thin."""
    strip_pct = config.get("title_block_region", {}).get("pct", 0.15)
    min_len = config.get("min_vector_text_length", 10)

    # Crop right strip
    rect_pct = (1.0 - strip_pct, 0.0, 1.0, 1.0)
    w, h = page.rect.width, page.rect.height
    clip = fitz.Rect((1.0 - strip_pct) * w, 0, w, h)
    strip_text = page.get_text("text", clip=clip) or ""

    # Also get full page text — sheet number may not be in the strip alone
    full_text = page.get_text("text") or ""

    if len(strip_text.strip()) >= min_len or len(full_text.strip()) >= min_len:
        info = _parse_vector_strip(strip_text + "\n" + full_text)
        info.source = "vector"
        if info.sheet_number:
            info.sheet_number = normalize_sheet_number(info.sheet_number)
            return info

    # Fall back to vision
    if ai_client:
        try:
            img_bytes = crop_region_image(page, rect_pct, dpi=200)
            info = _vision_extract(img_bytes, ai_client)
            info.source = "vision"
            info.sheet_number = normalize_sheet_number(info.sheet_number)
            return info
        except Exception:
            pass

    return TitleBlockInfo(source="vector")


def _parse_vector_strip(text: str) -> TitleBlockInfo:
    info = TitleBlockInfo()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Sheet number — use broad regex to catch variations
    sheet_re = re.compile(r'^[A-Z]{1,3}\s*[-\s]?\s*\d{1,4}(?:[.\s]\d{1,2})?$')
    for line in reversed(lines):
        if sheet_re.match(line.strip()) and len(line.strip()) <= 12:
            info.sheet_number = line.strip()
            break

    # Date
    dm = _DATE_RE.search(text)
    if dm:
        info.date = dm.group(0)

    # Status keywords
    for kw in ("FINAL BID SET", "BID SET", "CONSTRUCTION DOCUMENTS", "ISSUED FOR BID"):
        if kw in text.upper():
            info.status = kw
            break

    # Contract code (e.g. HBT-G2, HBT-P2)
    contract_m = re.search(r'\b([A-Z]{2,6}-[A-Z]\d+)\b', text)
    if contract_m:
        info.contract = contract_m.group(1)

    # Sheet title: usually the second-to-last or last non-number line
    title_candidates = [ln for ln in reversed(lines) if len(ln) > 5 and not sheet_re.match(ln)]
    for candidate in title_candidates:
        if any(kw in candidate.upper() for kw in ("PLAN", "SECTION", "DETAIL", "ELEVATION", "SCHEDULE", "NOTES")):
            info.sheet_title = candidate
            break

    return info


def _vision_extract(image_bytes: bytes, ai_client) -> TitleBlockInfo:
    import json
    prompt = (
        "Extract from this architectural title block strip. "
        "Return ONLY a JSON object with keys: "
        "sheet_number, sheet_title, project_name, contract, status, date. "
        "Use empty string for any field not found."
    )
    try:
        raw = ai_client.interpret_image_region(image_bytes, prompt)
        data = json.loads(raw)
        return TitleBlockInfo(
            sheet_number=data.get("sheet_number", ""),
            sheet_title=data.get("sheet_title", ""),
            project_name=data.get("project_name", ""),
            contract=data.get("contract", ""),
            status=data.get("status", ""),
            date=data.get("date", ""),
        )
    except Exception:
        return TitleBlockInfo()
