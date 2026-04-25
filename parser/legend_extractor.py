"""Extract work items from LEGEND tables — operates on pre-segmented zones.

Token efficiency:
- Crops the legend rectangle from `SheetZones` at 150 DPI instead of the full
  page (typically 5-10x fewer tokens per call).
- Uses a fixed JSON-schema prompt so Anthropic's prompt cache reuses it
  across every legend on every sheet.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

import fitz

from parser.zone_segmenter import SheetZones, Zone, crop_zone_png


# Keep this string byte-stable so the system prompt cache stays warm.
_LEGEND_PROMPT = (
    "Extract every work item from this LEGEND table on an architectural "
    "drawing. A legend lists scope items keyed to a symbol/letter and "
    "describes what work the GC must perform. "
    "Return ONLY a JSON array (no markdown fences, no preamble) of objects "
    'with this exact schema: '
    '[{"work_description": string, "detail_refs": [string], "units": string, '
    '"qty": number | null}]. '
    "Rules:\n"
    "- work_description: the full scope sentence verbatim (preserve case).\n"
    "- detail_refs: any '#/SHEET' callouts (e.g. '4/A401') referenced inline.\n"
    "- units: SF, LF, EA, LS, CY — default to 'EA' if absent.\n"
    "- qty: only set if a numeric quantity appears next to the item; otherwise null.\n"
    "Skip items that are pure references (e.g. 'SEE NOTES'), legend keys "
    "without scope verbs, or items that describe existing conditions only."
)


def extract_legend_items(
    page: fitz.Page,
    ai_client,
    zones: Optional[SheetZones] = None,
) -> list[dict]:
    """Backward-compatible wrapper: if zones are not supplied, segment now.

    Returns list of dicts:
        {work_description, detail_refs, units, qty}
    """
    if ai_client is None:
        return []

    legend_zones: Iterable[Zone]
    if zones is not None:
        legend_zones = zones.legends
    else:
        # Segment on demand so existing callers keep working during the
        # transition.
        from parser.zone_segmenter import segment
        legend_zones = segment(page).legends

    out: list[dict] = []
    for z in legend_zones:
        try:
            img_bytes = crop_zone_png(page, z, dpi=150)
        except Exception:
            continue
        out.extend(_extract_one(img_bytes, ai_client))

    if not out:
        # Last-ditch fallback: legacy full-page legend extraction. This still
        # uses the cached prompt, so token cost stays bounded.
        try:
            from parser.pdf_splitter import get_page_image
            img_bytes = get_page_image(page, dpi=150)
            out.extend(_extract_one(img_bytes, ai_client))
        except Exception:
            pass

    return out


def _extract_one(image_bytes: bytes, ai_client) -> list[dict]:
    try:
        raw = ai_client.extract_legend_from_image(image_bytes, _LEGEND_PROMPT)
        items = json.loads(_strip_fences(raw))
        if not isinstance(items, list):
            return []
        out: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            qty = item.get("qty")
            try:
                qty = float(qty) if qty not in (None, "", "null") else None
            except Exception:
                qty = None
            out.append({
                "work_description": str(item.get("work_description", "")).strip(),
                "detail_refs": list(item.get("detail_refs") or []),
                "units": (str(item.get("units") or "EA").strip() or "EA"),
                "qty": qty,
            })
        return out
    except Exception:
        return []


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        parts = s.split("```", 2)
        if len(parts) >= 2:
            inner = parts[1]
            if inner.lower().startswith("json"):
                inner = inner[4:]
            return inner.strip()
    return s
