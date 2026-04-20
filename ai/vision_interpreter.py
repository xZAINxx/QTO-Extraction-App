"""High-level vision interpretation for legend and title block regions."""
import json

from parser.pdf_splitter import get_page_image, crop_region_image
import fitz


class VisionInterpreter:
    def __init__(self, ai_client):
        self._client = ai_client

    def interpret_legend(self, page: fitz.Page, region_pct: tuple | None = None) -> list[dict]:
        """Extract symbol legend items via Vision."""
        try:
            if region_pct:
                img = crop_region_image(page, region_pct)
            else:
                img = get_page_image(page)
            prompt = (
                "Extract the floor plan legend or symbol key from this drawing region. "
                "Return ONLY a JSON array. Each object: "
                '{"symbol": str, "description": str}. '
                "No preamble, no markdown fences."
            )
            raw = self._client.interpret_image_region(img, prompt)
            return json.loads(raw)
        except Exception:
            return []

    def interpret_title_block(self, page: fitz.Page, strip_pct: float = 0.15) -> dict:
        """Extract title block fields via Vision from the right strip."""
        try:
            img = crop_region_image(page, (1.0 - strip_pct, 0.0, 1.0, 1.0), dpi=200)
            prompt = (
                "Extract fields from this architectural title block. "
                "Return ONLY a JSON object with keys: "
                "sheet_number, sheet_title, project_name, contract, status, date. "
                "Empty string for missing fields."
            )
            raw = self._client.interpret_image_region(img, prompt)
            return json.loads(raw)
        except Exception:
            return {}
