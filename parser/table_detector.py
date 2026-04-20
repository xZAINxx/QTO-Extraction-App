"""Find all tabular regions on a page and classify each as Type A/B/C/D."""
import re
from dataclasses import dataclass, field
from typing import Optional

import fitz
import pdfplumber


_TYPE_A_HEADERS = re.compile(
    r'(KEY\s*NOTES?|KEYED\s+\w*\s*NOTES?|KEYNOTE|GENERAL\s+NOTES?)', re.IGNORECASE
)
_TYPE_B_HEADERS = re.compile(r'(LEGEND|SYMBOL)', re.IGNORECASE)
_TYPE_C_HEADERS = re.compile(r'SCHEDULE', re.IGNORECASE)
_TYPE_D_HEADERS = re.compile(r'(COUNT|TOTALS?|QUANTITIES|SUMMARY)', re.IGNORECASE)


@dataclass
class TableRegion:
    table_type: str           # A | B | C | D
    header_text: str
    bbox: Optional[tuple] = None     # (x0, y0, x1, y1) in PDF points
    rows: list[list[str]] = field(default_factory=list)
    raw_text: str = ""


def detect_tables(page: fitz.Page, pdf_path: str, page_num: int) -> list[TableRegion]:
    """Find all table regions on a page."""
    regions: list[TableRegion] = []
    page_text = page.get_text("text") or ""

    # Use pdfplumber for structured table extraction
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pl_page = pdf.pages[page_num - 1]
            tables = pl_page.extract_tables()
            for table in (tables or []):
                if not table or not table[0]:
                    continue
                header = " ".join(str(c) for c in table[0] if c)
                region = _classify_table(header, table)
                if region:
                    regions.append(region)
    except Exception:
        pass

    # Supplement with text-based detection for regions pdfplumber might miss
    _detect_text_based_regions(page_text, regions)

    # Also extract numbered note lists from raw text
    note_regions = extract_numbered_notes_from_text(page_text, _TYPE_A_HEADERS)
    for nr in note_regions:
        if not any(r.header_text == nr.header_text for r in regions):
            regions.append(nr)

    return regions


def _classify_table(header: str, rows: list[list]) -> Optional[TableRegion]:
    if _TYPE_A_HEADERS.search(header):
        return TableRegion(table_type="A", header_text=header, rows=rows)
    if _TYPE_D_HEADERS.search(header):
        return TableRegion(table_type="D", header_text=header, rows=rows)
    if _TYPE_C_HEADERS.search(header):
        return TableRegion(table_type="C", header_text=header, rows=rows)
    if _TYPE_B_HEADERS.search(header):
        return TableRegion(table_type="B", header_text=header, rows=rows)
    # Check if first column looks like keynote IDs
    if len(rows) > 2:
        col0 = [r[0] for r in rows[1:] if r and r[0]]
        if col0 and all(re.match(r'^[\w()-]{1,10}$', str(c).strip()) for c in col0[:5]):
            return TableRegion(table_type="A", header_text=header, rows=rows)
    return None


def _detect_text_based_regions(page_text: str, existing: list[TableRegion]):
    """Find Type A/D tables in raw text that pdfplumber may have missed."""
    lines = page_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if _TYPE_A_HEADERS.search(line) and not any(r.header_text == line for r in existing):
            # Collect subsequent lines that look like "ID description" pairs
            rows = []
            j = i + 1
            while j < len(lines) and j < i + 60:
                l = lines[j].strip()
                if not l:
                    j += 1
                    continue
                m = re.match(r'^([\w()\-]{1,10})\s{2,}(.+)$', l)
                if m:
                    rows.append([m.group(1), m.group(2)])
                elif rows:
                    break
                j += 1
            if rows:
                existing.append(TableRegion(
                    table_type="A",
                    header_text=line,
                    rows=rows,
                    raw_text="\n".join(lines[i:j]),
                ))
        elif _TYPE_D_HEADERS.search(line) and not any(r.header_text == line for r in existing):
            rows = []
            j = i + 1
            while j < len(lines) and j < i + 20:
                l = lines[j].strip()
                m = re.match(r'^(.+?):\s*(\d[\d,\s\(\)]*)', l)
                if m:
                    rows.append([m.group(1).strip(), m.group(2).strip()])
                elif rows:
                    break
                j += 1
            if rows:
                existing.append(TableRegion(
                    table_type="D",
                    header_text=line,
                    rows=rows,
                ))
        i += 1


def extract_numbered_notes_from_text(text: str, header_pattern: re.Pattern) -> list[TableRegion]:
    """Extract numbered note lists from raw page text."""
    regions = []
    lines = text.splitlines()

    # Find all note header positions
    header_positions = []
    for i, line in enumerate(lines):
        if header_pattern.search(line.strip()):
            header_positions.append((i, line.strip()))

    for pos, header in header_positions:
        rows = []
        current_id = None
        current_text_parts = []
        j = pos + 1

        while j < len(lines) and j < pos + 80:
            line = lines[j].strip()
            if not line:
                j += 1
                continue

            # New numbered item
            num_m = re.match(r'^(\d+)[.\s]\s*(.+)', line)
            if num_m:
                if current_id is not None and current_text_parts:
                    rows.append([current_id, " ".join(current_text_parts)])
                current_id = num_m.group(1)
                current_text_parts = [num_m.group(2)]
            elif current_id is not None:
                # Continuation of previous item
                # Stop if looks like a new section header
                if re.match(r'^[A-Z][A-Z\s]{5,}$', line) or header_pattern.search(line):
                    break
                current_text_parts.append(line)
            else:
                # Haven't found first item yet — skip lead lines (like blank rows after header)
                pass

            j += 1

        if current_id is not None and current_text_parts:
            rows.append([current_id, " ".join(current_text_parts)])

        if rows:
            regions.append(TableRegion(table_type="A", header_text=header, rows=rows))

    return regions
