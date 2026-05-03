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
    # risk_flags is a per-row taxonomy of estimator-facing risks. The
    # DataTable's RiskFlagsDelegate (commit 10) paints one short pill per
    # entry. Allowed values come from a fixed taxonomy:
    #   "spec_ambiguity"      — yellow pill (warning)
    #   "design_dev_drawing"  — amber pill (warning, design-development)
    #   "volatile_material"   — red pill (danger)
    #   "low_qty_confidence"  — neutral pill (info)
    #   "by_others"           — info-slate pill (NIC, "not in contract")
    # Persists via dataclass JSON through ``ResultCache.save``; older
    # cached payloads round-trip with an empty list by default.
    risk_flags: list[str] = field(default_factory=list)
