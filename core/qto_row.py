from dataclasses import dataclass, field


@dataclass
class QTORow:
    s_no: int = 0
    tag: str = ""
    drawings_details: str = ""
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
