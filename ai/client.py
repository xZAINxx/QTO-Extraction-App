"""Anthropic client with prompt caching, token tracking, and session description cache."""
import base64
import os
from pathlib import Path
from typing import Any, Optional

import anthropic

from core.token_tracker import TokenTracker


_SYSTEM_EXTRACTION = """You are a construction document parser for Quantity Takeoff (QTO) extraction.
You analyze architectural and engineering drawing sheets and extract structured data.

Output ONLY valid JSON — no markdown fences, no preamble, no explanation.
All string values must be properly escaped JSON strings."""

_SYSTEM_CLASSIFY = """You are a construction specification classifier.
Classify descriptions into CSI MasterFormat divisions.
Output ONLY a JSON object with key "division" (e.g. "DIVISION 02") and "confidence" (0.0-1.0).
No markdown, no preamble."""


class AIClient:
    def __init__(self, config: dict, tracker: TokenTracker):
        api_key = config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = config.get("model", "claude-sonnet-4-6")
        self._max_tokens = config.get("max_tokens_per_page_call", 8000)
        self._tracker = tracker
        self._classify_cache: dict[str, tuple[str, float]] = {}
        self._compose_cache: dict[str, str] = {}

    def _call(self, system: str, messages: list, max_tokens: int | None = None) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens or self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
        self._tracker.record(resp.usage)
        return resp.content[0].text

    def classify_csi(self, description: str, fallback_keywords: dict) -> tuple[str, float]:
        """Return (csi_division_label, confidence). Cached per description."""
        key = description.lower().strip()
        if key in self._classify_cache:
            return self._classify_cache[key]

        import json
        try:
            raw = self._call(
                _SYSTEM_CLASSIFY,
                [{"role": "user", "content": f"Classify this construction item:\n{description}"}],
                max_tokens=256,
            )
            parsed = json.loads(raw)
            division = parsed.get("division", "")
            confidence = float(parsed.get("confidence", 0.7))
        except Exception:
            division, confidence = _keyword_classify(description, fallback_keywords), 0.5

        self._classify_cache[key] = (division, confidence)
        return division, confidence

    def compose_description(self, raw: str, sheet: str = "", keynote_ref: str = "") -> str:
        """Compose a GC-estimate-grade description from raw keynote text. Cached per (raw, sheet, keynote_ref)."""
        from ai.description_normalizer import _SYSTEM
        raw = raw.strip()
        cache_key = f"{raw}|{sheet}|{keynote_ref}"
        if cache_key in self._compose_cache:
            return self._compose_cache[cache_key]
        user_content = f"Sheet: {sheet}\nKeynote: {keynote_ref}\nRaw: {raw}"
        try:
            result = self._call(_SYSTEM, [{"role": "user", "content": user_content}], max_tokens=256).strip()
        except Exception:
            result = raw.upper()
        self._compose_cache[cache_key] = result
        return result

    def interpret_image_region(self, image_bytes: bytes, prompt: str) -> str:
        """Send a cropped image region to Claude Vision. Returns raw text."""
        b64 = base64.standard_b64encode(image_bytes).decode()
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_EXTRACTION,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        self._tracker.record(resp.usage)
        return resp.content[0].text

    def classify_scope_vs_reference(self, note_text: str) -> str:
        """Return 'scope' or 'reference'."""
        try:
            raw = self._call(
                "Classify this construction note as either 'scope' (describes actual work to be done: "
                "install, furnish, provide, remove, patch, coordinate) or 'reference' (cites standards, "
                "codes, or describes existing conditions). Output ONLY the word 'scope' or 'reference'.",
                [{"role": "user", "content": note_text}],
                max_tokens=16,
            )
            return raw.strip().lower()
        except Exception:
            return "scope"  # default conservative

    def extract_page_claude_only(self, image_bytes: bytes) -> list[dict]:
        """
        claude_only mode: send full page image to Claude, get back structured extraction.
        Returns list of {id, description, units, qty, table_type} dicts.
        """
        import json
        prompt = (
            "Extract all construction work items from this architectural drawing sheet. "
            "Look for: keynote tables, general notes, scope notes, schedules, count tables. "
            "For each item return a JSON object with: "
            '{"id": str, "description": str, "qty": number, "units": str, "table_type": "A"|"C"|"D"}. '
            "Use EA for each, LS for lump sum, SF for square feet, LF for linear feet. "
            "Return ONLY a JSON array. No preamble, no markdown fences."
        )
        try:
            b64 = base64.standard_b64encode(image_bytes).decode()
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_EXTRACTION,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            self._tracker.record(resp.usage)
            return json.loads(resp.content[0].text)
        except Exception:
            return []


def _keyword_classify(description: str, keywords: dict) -> str:
    lower = description.lower()
    for division, kws in keywords.items():
        if any(kw in lower for kw in kws):
            return division
    return "DIVISION 09"  # default to finishes if unknown
