from dataclasses import dataclass, field


@dataclass
class QTORow:
    s_no: int = 0
    tag: str = ""
    drawings: str = ""
    details: str = ""
    math_trail: str = ""
    description: str = ""
    qty: float = 0.0
    units: str = ""
    unit_price: float = 0.0
    total_formula: str = ""
    trade_division: str = ""
    is_header_row: bool = False
    source_page: int = 0
    source_sheet: str = ""
    extraction_method: str = ""   # vector | vision | schedule | summary_table
    confidence: float = 1.0
    needs_review: bool = False
    # Wave 2 — UI redesign extensions (backward-compatible defaults).
    # bbox is the row's source bounding box on the PDF page in mediabox
    # coordinates (x0, y0, x1, y1); used by the trace-back overlay
    # (commit 6) to highlight the originating region when the user
    # clicks the row. ``None`` until the extractors learn to populate it.
    bbox: tuple[float, float, float, float] | None = None
    # confirmed is the "yellow-confirm" estimator gesture: pressing Y or
    # clicking a green StatusPill stamps the row as human-validated. The
    # DataTable paints confirmed rows in the construction-yellow domain
    # color. Persists across sessions via ``ResultCache.save``.
    confirmed: bool = False
