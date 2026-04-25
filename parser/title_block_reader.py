"""Rotation-aware sheet-metadata extractor.

Reads the title-block strip (always the right-side ~18% of the *displayed*
page) and pulls out: sheet_number, sheet_title, project_name, contract,
status, date.

The reader inspects raw spans via ``page.get_text("rawdict")`` so it can
filter by mediabox position regardless of page rotation, and pick the
biggest font-size span matching the sheet-number pattern. If vector text
yields nothing useful (common for rasterized title blocks like the
Brooklyn set), a rendered-strip vision crop is sent to Sonnet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import fitz


_SHEET_NUM_RE = re.compile(
    r'^[A-Z]{1,3}\s*[-\s]?\s*\d{1,4}(?:[.\s]\d{1,2})?$'
)
_DATE_RE = re.compile(
    r'\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{4}\b',
    re.IGNORECASE,
)


def normalize_sheet_number(sheet: str) -> str:
    """A107.00 → A-107. R-001 / T-002 / S5.1 pass through gracefully."""
    sheet = sheet.strip()
    m = re.match(r'^([A-Za-z]{1,3})\s*[-\s]?\s*(\d{1,4})(?:\.\d+)?$', sheet)
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
    source: str = "vector"   # vector | vision | rotated_vision


def read_title_block(
    page: fitz.Page,
    config: dict,
    ai_client=None,
) -> TitleBlockInfo:
    strip_pct = config.get("title_block_region", {}).get("pct", 0.18)
    rotation = page.rotation
    page_rect = page.rect

    # 1) Rotation-aware vector-text scan.
    info = _vector_scan(page, strip_pct=strip_pct, rotation=rotation)
    if info.sheet_number:
        info.source = "vector"
        info.sheet_number = normalize_sheet_number(info.sheet_number)
        return info

    # 2) Vision fallback on a tightly-cropped rotated strip.
    if ai_client is not None:
        try:
            tb_rect = _title_block_rect(page_rect, strip_pct)
            mat = fitz.Matrix(200 / 72, 200 / 72)
            pm = page.get_pixmap(matrix=mat, clip=tb_rect, alpha=False)
            img_bytes = pm.tobytes("png")
            v_info = _vision_extract(img_bytes, ai_client)
            v_info.source = "vision"
            if v_info.sheet_number:
                v_info.sheet_number = normalize_sheet_number(v_info.sheet_number)
            # Merge any vector fields we did parse (project, date, etc.)
            for fld in ("project_name", "date", "status", "contract", "sheet_title"):
                if not getattr(v_info, fld) and getattr(info, fld):
                    setattr(v_info, fld, getattr(info, fld))
            return v_info
        except Exception:
            pass

    return info


# ── Vector-text scan ───────────────────────────────────────────────────────


def _vector_scan(page: fitz.Page, strip_pct: float, rotation: int) -> TitleBlockInfo:
    """Iterate all spans, score them, return best title-block info."""
    info = TitleBlockInfo()
    rd = page.get_text("rawdict")

    # Compute mediabox bounds for the title-block strip given page rotation.
    # rawdict bboxes are in mediabox space, regardless of page.rotation.
    mb = page.mediabox
    mw, mh = mb.width, mb.height
    if rotation in (90, 270):
        # Rotated 90° (CW or CCW): displayed right-strip = top-or-bottom strip
        # in mediabox depending on rotation direction.
        if rotation == 270:
            tb_x_min, tb_y_min, tb_x_max, tb_y_max = (
                0.0, mh * (1 - strip_pct), mw, mh,
            )
        else:  # 90
            tb_x_min, tb_y_min, tb_x_max, tb_y_max = (
                0.0, 0.0, mw, mh * strip_pct,
            )
    elif rotation == 180:
        tb_x_min, tb_y_min, tb_x_max, tb_y_max = (
            0.0, 0.0, mw * strip_pct, mh,
        )
    else:  # 0
        tb_x_min, tb_y_min, tb_x_max, tb_y_max = (
            mw * (1 - strip_pct), 0.0, mw, mh,
        )

    spans: list[tuple[float, str, tuple, tuple]] = []
    for block in rd.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = "".join(c.get("c", "") for c in span.get("chars", []))
                txt = txt.strip()
                if not txt:
                    continue
                bb = span["bbox"]
                if not _bbox_in(bb, tb_x_min, tb_y_min, tb_x_max, tb_y_max):
                    continue
                spans.append((span.get("size", 0.0), txt, span.get("dir", (1, 0)), bb))

    if not spans:
        # Fall back to ANY span anywhere on page — matches H-series sheets
        # whose number span happens to bleed outside the strip.
        for block in rd.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    txt = "".join(c.get("c", "") for c in span.get("chars", [])).strip()
                    if not txt:
                        continue
                    spans.append((span.get("size", 0.0), txt, span.get("dir", (1, 0)), span["bbox"]))

    spans.sort(key=lambda s: -s[0])

    # Pick biggest span whose normalized text matches a sheet-number pattern.
    # Sheet numbers in this format (Brooklyn / SCA / NYCSCA) are rendered at
    # ≥ 24 pt; smaller hits are usually building IDs, dates, or revision tags.
    SHEET_MIN_SIZE = 24.0
    for size, txt, _dir, _bb in spans:
        if size < SHEET_MIN_SIZE:
            break
        norm = txt.replace(" ", "")
        if _SHEET_NUM_RE.match(norm) and len(norm) <= 12:
            info.sheet_number = txt
            break

    # Date / status / contract / sheet_title — search whole strip text, not
    # just one span.
    strip_text = "\n".join(s[1] for s in spans)
    dm = _DATE_RE.search(strip_text)
    if dm:
        info.date = dm.group(0)
    for kw in ("FINAL BID SET", "BID SET", "BID SUBMISSION",
               "CONSTRUCTION DOCUMENTS", "ISSUED FOR BID", "FOR REVIEW"):
        if kw in strip_text.upper():
            info.status = kw
            break
    contract_m = re.search(r'\b([A-Z]{2,6}-[A-Z]\d+)\b', strip_text)
    if contract_m:
        info.contract = contract_m.group(1)

    # Sheet title — biggest non-sheet-number span containing PLAN / SECTION / etc.
    for size, txt, _dir, _bb in spans:
        if any(kw in txt.upper() for kw in
               ("PLAN", "SECTION", "DETAIL", "ELEVATION", "SCHEDULE",
                "NOTES", "ABBREVIATIONS", "LEGEND")) and len(txt) > 4:
            info.sheet_title = txt
            break

    return info


def _bbox_in(bb, x_min, y_min, x_max, y_max) -> bool:
    cx = (bb[0] + bb[2]) / 2
    cy = (bb[1] + bb[3]) / 2
    return x_min <= cx <= x_max and y_min <= cy <= y_max


def _title_block_rect(page_rect: fitz.Rect, strip_pct: float = 0.18) -> fitz.Rect:
    """Title-block strip in displayed (rotated) page coordinates."""
    w = page_rect.width
    return fitz.Rect(page_rect.x0 + w * (1 - strip_pct),
                     page_rect.y0, page_rect.x1, page_rect.y1)


# ── Vision fallback ────────────────────────────────────────────────────────


def _vision_extract(image_bytes: bytes, ai_client) -> TitleBlockInfo:
    import json
    prompt = (
        "Extract from this architectural title block strip. "
        "Return ONLY a JSON object (no markdown fences) with keys: "
        '{"sheet_number","sheet_title","project_name","contract","status","date"}. '
        "sheet_number is the drawing number in formats like A-106, T-002, "
        "S-101, R-001, A901.00, M-201. "
        "Use empty string for any field not visible."
    )
    try:
        raw = ai_client.extract_title_block_vision(image_bytes, prompt)
        data = json.loads(_strip_fences(raw))
        return TitleBlockInfo(
            sheet_number=str(data.get("sheet_number", "")).strip(),
            sheet_title=str(data.get("sheet_title", "")).strip(),
            project_name=str(data.get("project_name", "")).strip(),
            contract=str(data.get("contract", "")).strip(),
            status=str(data.get("status", "")).strip(),
            date=str(data.get("date", "")).strip(),
        )
    except Exception:
        return TitleBlockInfo()


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s.lstrip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    return s.strip()
