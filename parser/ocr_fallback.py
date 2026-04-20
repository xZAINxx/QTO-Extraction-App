"""Claude Vision fallback for regions where vector extraction fails."""
import json
from typing import Optional

import fitz

from parser.pdf_splitter import crop_region_image, get_page_image


def extract_keynote_table_vision(
    page: fitz.Page,
    ai_client,
    region_pct: Optional[tuple] = None,
) -> list[dict]:
    """
    Use Claude Vision to extract a keynote table from a page region.
    Returns list of {id, description} dicts.
    """
    try:
        if region_pct:
            img = crop_region_image(page, region_pct)
        else:
            img = get_page_image(page)

        prompt = (
            "Extract the keynote/key-notes table from this drawing sheet region. "
            "Return ONLY a JSON array. Each object: "
            '{"id": str, "description": str}. '
            "The id is the keynote identifier (e.g. P-01, D-01, 1, 1A). "
            "No preamble, no markdown fences."
        )
        raw = ai_client.interpret_image_region(img, prompt)
        items = json.loads(raw)
        return [{"id": item.get("id", ""), "description": item.get("description", "")} for item in items]
    except Exception:
        return []


def extract_general_notes_vision(page: fitz.Page, ai_client) -> list[str]:
    """Extract numbered general notes as a list of strings."""
    try:
        img = get_page_image(page)
        prompt = (
            "Extract all numbered or lettered general/scope notes from this drawing sheet. "
            "Return ONLY a JSON array of strings — one string per note item. "
            "No markdown fences, no preamble."
        )
        raw = ai_client.interpret_image_region(img, prompt)
        return json.loads(raw)
    except Exception:
        return []
