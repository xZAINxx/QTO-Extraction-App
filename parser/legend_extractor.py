"""Extract work items from LEGEND tables via Vision."""
import json

import fitz


def extract_legend_items(page: fitz.Page, ai_client) -> list[dict]:
    """Crop full page and use Vision to extract LEGEND work items.

    Returns list of dicts with work_description, detail_refs, units.
    Returns empty list if ai_client is None or Vision fails.
    """
    if ai_client is None:
        return []
    try:
        from parser.pdf_splitter import get_page_image
        img_bytes = get_page_image(page, dpi=150)

        prompt = (
            "This is an architectural drawing sheet. Find the LEGEND table "
            "(typically bottom or side panel with work scope descriptions). "
            "Extract each work item from the legend. "
            "Return ONLY a JSON array. Each object: "
            '{"work_description": str, "detail_refs": [str], "units": str}. '
            'Set units to "EA" if not specified. '
            'Set detail_refs to [] if no detail callouts found. '
            "No preamble, no markdown fences."
        )
        raw = ai_client.interpret_image_region(img_bytes, prompt)
        items = json.loads(raw)
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            result.append({
                "work_description": str(item.get("work_description", "")),
                "detail_refs": list(item.get("detail_refs") or []),
                "units": str(item.get("units") or "EA") or "EA",
            })
        return result
    except Exception:
        return []
