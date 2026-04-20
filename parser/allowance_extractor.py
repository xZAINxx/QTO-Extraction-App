"""Extract ALLOWANCES and PROVISIONS from T-002 sheets via pdfplumber + Vision fallback."""
import json

import fitz


def extract_allowances(page: fitz.Page, sheet_info, ai_client=None) -> list[dict]:
    """Extract items from T-002 ALLOWANCES/PROVISIONS tables.

    Tries pdfplumber first; falls back to Vision if fewer than 2 items found.
    Returns empty list if both fail.
    """
    items = _try_pdfplumber(page)
    if len(items) < 2 and ai_client is not None:
        items = _try_vision(page, ai_client)
    return items


def _try_pdfplumber(page: fitz.Page) -> list[dict]:
    try:
        import pdfplumber
        pdf_path = page.parent.name
        page_num = page.number
        with pdfplumber.open(pdf_path) as pdf:
            pl_page = pdf.pages[page_num]
            tables = pl_page.extract_tables()
            if not tables:
                return []
            items = []
            for table in tables:
                section = None
                for row in table:
                    if not row:
                        continue
                    cells = [str(c).strip() if c else "" for c in row]
                    row_text = " ".join(c for c in cells if c).upper()
                    if "ALLOWANCE" in row_text and not any(c for c in cells[1:] if c):
                        section = "ALLOWANCE"
                        continue
                    elif "PROVISION" in row_text and not any(c for c in cells[1:] if c):
                        section = "PROVISION"
                        continue
                    num = cells[0] if cells else ""
                    desc = cells[1] if len(cells) > 1 else (cells[0] if cells else "")
                    if desc and section:
                        ref_type = "ALLOWANCES" if section == "ALLOWANCE" else "PROVISIONS"
                        items.append({
                            "description": f"({section}) {desc.upper()}",
                            "detail_refs": [f"{ref_type}# {num}/T002"] if num else [],
                            "units": "LS",
                            "qty": 1,
                        })
            return items
    except Exception:
        return []


def _try_vision(page: fitz.Page, ai_client) -> list[dict]:
    try:
        from parser.pdf_splitter import get_page_image
        img_bytes = get_page_image(page, dpi=150)
        prompt = (
            "This is a T-002 ALLOWANCES/PROVISIONS sheet from construction drawings. "
            "Extract all items from the ALLOWANCES and PROVISIONS tables. "
            "Return ONLY a JSON array. Each object: "
            '{"description": str, "section": "ALLOWANCE" or "PROVISION", "number": int_or_null}. '
            "No preamble, no markdown fences."
        )
        raw = ai_client.interpret_image_region(img_bytes, prompt)
        items_raw = json.loads(raw)
        items = []
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            section = str(item.get("section", "ALLOWANCE")).upper()
            desc = str(item.get("description", "")).upper()
            num = item.get("number")
            ref_type = "ALLOWANCES" if "ALLOWANCE" in section else "PROVISIONS"
            items.append({
                "description": f"({section}) {desc}",
                "detail_refs": [f"{ref_type}# {num}/T002"] if num else [],
                "units": "LS",
                "qty": 1,
            })
        return items
    except Exception:
        return []
