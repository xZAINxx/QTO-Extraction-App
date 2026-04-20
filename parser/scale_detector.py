"""Detect and parse the scale annotation from a drawing page."""
import re
from typing import Optional

import fitz


_SCALE_RE = re.compile(
    r"SCALE\s*[:\s=]\s*([\d/]+\"\s*=\s*[\d''\-\s]+\d+\"|\d+:\d+|N\.?T\.?S\.?|AS\s*SHOWN)",
    re.IGNORECASE,
)

_FRACTION_RE = re.compile(r"(\d+)/(\d+)\"?\s*=\s*1'")


def detect_scale(page: fitz.Page) -> Optional[float]:
    """
    Return pixels-per-foot scale factor, or None if NTS/AS SHOWN.
    Scale factor = PDF units per real foot.
    """
    text = page.get_text("text") or ""
    m = _SCALE_RE.search(text)
    if not m:
        return None

    scale_str = m.group(1).strip().upper()
    if "N.T.S" in scale_str or "NTS" in scale_str or "AS SHOWN" in scale_str:
        return None

    # Try fraction format: e.g. "3/16" = 1'-0"
    fm = _FRACTION_RE.search(scale_str)
    if fm:
        numerator = int(fm.group(1))
        denominator = int(fm.group(2))
        # scale_str like "3/16" means 3/16 inch on paper = 1 foot real
        # 1 inch = 72 PDF units; so 1 foot real = (3/16) * 72 PDF units
        pdf_units_per_foot = (numerator / denominator) * 72.0
        return pdf_units_per_foot

    # Try ratio format: e.g. "1:48"
    ratio_m = re.match(r"(\d+):(\d+)", scale_str)
    if ratio_m:
        a, b = int(ratio_m.group(1)), int(ratio_m.group(2))
        # a:b means 1 unit on paper = b/a units real; convert to feet
        # PDF units are points (1/72 inch); real feet
        # a points on paper = b points real => scale = a/b points per point
        # b real points = b/72 real inches = b/864 real feet
        # a PDF points per (b/864) real feet => (a * 864 / b) PDF pts per real foot
        pdf_units_per_foot = (a * 864.0) / b
        return pdf_units_per_foot

    return None
