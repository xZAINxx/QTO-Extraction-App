"""Route each TableRegion to its appropriate extractor (Type A/B/C/D)."""
import re
from typing import Optional

import fitz

from parser.table_detector import TableRegion
from parser.keynote_format_infer import infer_keynote_pattern, count_callouts_on_page
from parser.title_block_reader import TitleBlockInfo


def extract_type_a(
    region: TableRegion,
    page: fitz.Page,
    sheet_info: TitleBlockInfo,
    ai_client=None,
) -> list[dict]:
    """Keyed notes: returns list of {id, description, qty} dicts."""
    rows = region.rows
    if not rows:
        return []

    # Find ID column (usually first non-header row, first non-empty cell)
    data_rows = [r for r in rows if r and any(c for c in r)]
    # Skip header row if first row matches header
    if data_rows and _TYPE_A_HEADERS.match(str(data_rows[0][0]).strip() if data_rows[0] else ""):
        data_rows = data_rows[1:]

    result = []
    id_values = []

    for row in data_rows:
        if not row:
            continue
        cells = [str(c).strip() if c else "" for c in row]

        # Handle packed single-cell rows (pdfplumber sometimes cramps "1 Description text...")
        if len(cells) == 1 and cells[0]:
            parsed = _parse_packed_cell(cells[0])
            for id_val, desc in parsed:
                if desc:
                    id_values.append(id_val)
                    result.append({"id": id_val, "description": desc, "qty": 0})
            continue

        # Normal 2-column table
        id_val = cells[0]
        desc = " ".join(c for c in cells[1:] if c)
        if not desc and len(cells) >= 2:
            # Try second cell
            desc = cells[1] if cells[1] else ""

        # If first cell contains both ID and description (e.g. "P-01 Remove brick...")
        if id_val and not desc:
            parsed = _parse_packed_cell(id_val)
            for pid, pdesc in parsed:
                if pdesc:
                    id_values.append(pid)
                    result.append({"id": pid, "description": pdesc, "qty": 0})
            continue

        if id_val and desc:
            id_values.append(id_val)
            result.append({"id": id_val, "description": desc, "qty": 0})

    # Infer keynote pattern and count callouts on the page
    pattern = infer_keynote_pattern(id_values)
    if pattern:
        page_text = page.get_text("text") or ""
        callout_counts = count_callouts_on_page(page_text, pattern)
        for item in result:
            count = callout_counts.get(item["id"], 0)
            item["qty"] = max(count, 1)  # at least 1 if it appears in the table

    return result


def extract_type_b(
    region: TableRegion,
    page: fitz.Page,
    sheet_info: TitleBlockInfo,
    ai_client=None,
    crop_fn=None,
) -> list[dict]:
    """Symbol/hatch legend: use Vision to extract items."""
    if ai_client is None:
        return []
    import json
    try:
        if crop_fn and region.bbox:
            img_bytes = crop_fn(page, region.bbox)
        else:
            from parser.pdf_splitter import get_page_image
            img_bytes = get_page_image(page, dpi=150)

        prompt = (
            "Return ONLY a JSON array. Each object: "
            '{"description": str, "hatch_type": str}. '
            "No preamble, no markdown fences."
        )
        raw = ai_client.interpret_image_region(img_bytes, prompt)
        items = json.loads(raw)
        return [{"description": item.get("description", ""), "qty": 1, "hatch_type": item.get("hatch_type", "")}
                for item in items]
    except Exception:
        return []


def extract_type_c(region: TableRegion, sheet_info: TitleBlockInfo, ai_client=None) -> list[dict]:
    """Schedule table: map columns to QTO fields."""
    rows = region.rows
    if not rows or len(rows) < 2:
        return []

    header = [str(c).strip().upper() if c else "" for c in rows[0]]
    result = []
    for row in rows[1:]:
        if not row or all(not c for c in row):
            continue
        cells = [str(c).strip() if c else "" for c in row]
        desc_idx = _find_col(header, ["DESCRIPTION", "TYPE", "SIZE", "MARK", "ROOM", "ITEM"])
        qty_idx = _find_col(header, ["QTY", "COUNT", "QUANTITY", "NO."])
        mark_idx = _find_col(header, ["MARK", "TAG", "ID"])

        desc = cells[desc_idx] if desc_idx is not None and desc_idx < len(cells) else " / ".join(c for c in cells if c)
        qty_raw = cells[qty_idx] if qty_idx is not None and qty_idx < len(cells) else "1"
        mark = cells[mark_idx] if mark_idx is not None and mark_idx < len(cells) else ""

        qty = _parse_qty(qty_raw)
        if desc:
            result.append({"id": mark, "description": desc, "qty": qty})
    return result


def extract_type_d(region: TableRegion, sheet_info: TitleBlockInfo) -> list[dict]:
    """Summary/count table: read category:qty pairs directly."""
    result = []
    for row in region.rows:
        if not row:
            continue
        cells = [str(c).strip() if c else "" for c in row]
        if len(cells) < 2:
            # Try splitting on colon
            if len(cells) == 1 and ":" in cells[0]:
                parts = cells[0].split(":", 1)
                cells = [parts[0].strip(), parts[1].strip()]
            else:
                continue
        label, val = cells[0], cells[1]
        if not label or not val:
            continue
        qty = _parse_qty(val)
        if qty > 0:
            result.append({"description": label, "qty": qty, "units": "EA"})
    return result


def _find_col(header: list[str], candidates: list[str]) -> Optional[int]:
    for cand in candidates:
        for i, h in enumerate(header):
            if cand in h:
                return i
    return None


def _parse_qty(raw: str) -> float:
    m = re.search(r'[\d,]+', raw.replace(",", ""))
    if m:
        try:
            return float(m.group(0).replace(",", ""))
        except ValueError:
            pass
    return 1.0


import re as _re
_TYPE_A_HEADERS = _re.compile(
    r'(KEY\s*NOTES?|KEYED\s+\w*\s*NOTES?|KEYNOTE|GENERAL\s+NOTES?)', _re.IGNORECASE
)

_PACKED_NOTE_RE = _re.compile(r'^(\d{1,3}[A-Z]?|[A-Z]{1,3}-\d{1,3}|[A-Z]\d{3})\s*[.\s]+(.+)', _re.DOTALL)


def _parse_packed_cell(text: str) -> list[tuple[str, str]]:
    """Parse a single cell that may contain 'ID description' or numbered notes."""
    text = text.strip()
    if not text:
        return []

    results = []
    # Try numbered list: "1. Description\n2. Description"
    items = _re.split(r'\n(?=\d+[.\s])', text)
    if len(items) > 1:
        for item in items:
            m = _PACKED_NOTE_RE.match(item.strip())
            if m:
                results.append((m.group(1), m.group(2).replace("\n", " ").strip()))
        if results:
            return results

    # Single "ID desc" pattern
    m = _PACKED_NOTE_RE.match(text)
    if m:
        return [(m.group(1), m.group(2).replace("\n", " ").strip())]

    # No ID found — treat whole text as a description with empty ID
    if len(text) > 10:
        return [("", text.replace("\n", " ").strip())]
    return []
