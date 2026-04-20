"""Split PDF into pages and classify each page type."""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import fitz  # pymupdf


PAGE_TYPES = {
    "PLAN_DEMO", "PLAN_CONSTRUCTION", "ELEVATION", "SCHEDULE",
    "DETAIL_WITH_SCOPE", "DETAIL", "LEGEND_ONLY", "TITLE_PAGE",
}

_SCOPE_KEYWORDS = re.compile(
    r"\b(install|provide|furnish|remove|patch|coordinate|replace|repair|"
    r"demolish|construct|apply|seal|attach|secure|clean|prime|paint)\b",
    re.IGNORECASE,
)

_REFERENCE_KEYWORDS = re.compile(
    r"\b(aisc|astm|ansi|code|reference|standard|section|per|per code|"
    r"building code|specification|nfpa)\b",
    re.IGNORECASE,
)

_DETAIL_SERIES = re.compile(r"^[A-Z]{1,3}\s*-?\s*[56]\d{2}", re.IGNORECASE)


@dataclass
class PageInfo:
    page_num: int       # 1-based
    page_type: str
    text: str
    skip: bool
    skip_reason: str = ""


def classify_page(page_num: int, text: str) -> PageInfo:
    t = text.upper()

    # Title page heuristic: short text, contains project/title info but no plan/schedule
    if (
        ("DRAWING INDEX" in t or "TITLE SHEET" in t or "COVER" in t)
        and "PLAN" not in t and "SCHEDULE" not in t
        and "NOTES" not in t
    ):
        return PageInfo(page_num, "TITLE_PAGE", text, skip=True, skip_reason="Cover/title sheet")

    if "DEMOLITION PLAN" in t or "DEMO PLAN" in t:
        return PageInfo(page_num, "PLAN_DEMO", text, skip=False)

    if "FLOOR PLAN" in t or "CONSTRUCTION PLAN" in t or "FRAMING PLAN" in t:
        return PageInfo(page_num, "PLAN_CONSTRUCTION", text, skip=False)

    if "ELEVATION" in t and "PLAN" not in t:
        return PageInfo(page_num, "ELEVATION", text, skip=False)

    if "SCHEDULE" in t:
        return PageInfo(page_num, "SCHEDULE", text, skip=False)

    # Detail sheet — check for scope notes
    is_detail_series = bool(_DETAIL_SERIES.search(text.split("\n")[0][:30] if text else ""))
    if "DETAIL" in t or is_detail_series:
        scope_hits = len(_SCOPE_KEYWORDS.findall(text))
        if scope_hits >= 3:
            return PageInfo(page_num, "DETAIL_WITH_SCOPE", text, skip=False)
        return PageInfo(page_num, "DETAIL", text, skip=True, skip_reason="Detail sheet, no scope notes")

    if "LEGEND" in t and "KEY" not in t and "NOTE" not in t:
        return PageInfo(page_num, "LEGEND_ONLY", text, skip=False)

    # Default — treat as construction plan and let extractor figure it out
    return PageInfo(page_num, "PLAN_CONSTRUCTION", text, skip=False)


def split_and_classify(
    pdf_path: str,
    cached_classifications: dict | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> Iterator[tuple[fitz.Page, PageInfo]]:
    """Yield (fitz_page, PageInfo) for each page in the PDF."""
    doc = fitz.open(pdf_path)
    total = doc.page_count
    for i in range(total):
        page = doc[i]
        page_num = i + 1

        if cached_classifications and str(page_num) in cached_classifications:
            cached = cached_classifications[str(page_num)]
            info = PageInfo(
                page_num=page_num,
                page_type=cached["page_type"],
                text=cached.get("text", ""),
                skip=cached["skip"],
                skip_reason=cached.get("skip_reason", ""),
            )
        else:
            text = page.get_text("text") or ""
            info = classify_page(page_num, text)

        if progress_cb:
            progress_cb(page_num, total, info.page_type)
        yield page, info
    doc.close()


def get_page_image(page: fitz.Page, dpi: int = 150) -> bytes:
    """Render a page to PNG bytes."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return pix.tobytes("png")


def crop_region_image(page: fitz.Page, rect_pct: tuple[float, float, float, float], dpi: int = 150) -> bytes:
    """Crop a region (x0%, y0%, x1%, y1%) and render to PNG bytes."""
    w, h = page.rect.width, page.rect.height
    x0, y0, x1, y1 = rect_pct
    clip = fitz.Rect(x0 * w, y0 * h, x1 * w, y1 * h)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
    return pix.tobytes("png")
