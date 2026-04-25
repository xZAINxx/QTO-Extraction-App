"""Anthropic model router with prompt caching and per-model token tracking.

Tier strategy:
- Haiku 4.5: classification (page type, scope-vs-reference, CSI fallback) — $1/M.
- Sonnet 4.6: description composition + targeted vision crops — $3/M.
- Opus 4.5: explicit fallback for `claude_only` mode + retries — $15/M.

All prompts that get reused (system prompts, few-shot blocks, JSON schemas)
are wrapped in `cache_control: {"type": "ephemeral"}` so subsequent calls in
the same session pay the 10% cache-read price.
"""
import base64
import json
import os
from typing import Any, Optional

import anthropic

from core.token_tracker import TokenTracker


_SYSTEM_EXTRACTION = """You are a construction document parser for Quantity Takeoff (QTO) extraction.
You analyze architectural and engineering drawing sheets and extract structured data.

Output ONLY valid JSON — no markdown fences, no preamble, no explanation.
All string values must be properly escaped JSON strings."""

_SYSTEM_CSI_CLASSIFY = """You are a construction specification classifier.
Classify descriptions into CSI MasterFormat divisions.
Output ONLY a JSON object with key "division" (e.g. "DIVISION 02") and "confidence" (0.0-1.0).
No markdown, no preamble."""

_SYSTEM_SCOPE_VS_REFERENCE = (
    "Classify this construction note as either 'scope' (describes actual work to be done: "
    "install, furnish, provide, remove, patch, coordinate) or 'reference' (cites standards, "
    "codes, or describes existing conditions). Output ONLY the word 'scope' or 'reference'."
)

_SYSTEM_PAGE_TYPE = (
    "You classify a single architectural drawing sheet by page type. "
    "Output ONLY one of: PLAN_DEMO, PLAN_CONSTRUCTION, ELEVATION, SCHEDULE, "
    "DETAIL_WITH_SCOPE, DETAIL, LEGEND_ONLY, TITLE_PAGE, ALLOWANCES_PROVISIONS. "
    "No preamble, no JSON."
)

_SYSTEM_DIFF = (
    "You compare two crops of the same region from two revisions of an architectural "
    "drawing sheet. The crops are aligned to the same coordinate frame. "
    "Your job: describe what changed in ONE concise, construction-relevant sentence. "
    "Focus on: added/removed/relocated symbols, dimension changes, callout edits, "
    "scope-note revisions, hatch/material changes. Ignore: anti-alias jitter, scan "
    "noise, line-weight tweaks, paper-texture differences. "
    'If nothing meaningful changed reply EXACTLY: "no meaningful change".'
)


class AIClient:
    """Routes per-task to the cheapest model that handles it well."""

    def __init__(self, config: dict, tracker: TokenTracker):
        api_key = config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._tracker = tracker

        models = config.get("models", {})
        self._haiku = models.get("haiku", "claude-haiku-4-5-20251001")
        self._sonnet = models.get("sonnet", "claude-sonnet-4-6")
        self._opus = models.get("opus", "claude-opus-4-5")
        # Legacy single-model fallback (some callers still read .model).
        self._model = config.get("model", self._sonnet)
        self._max_tokens = config.get("max_tokens_per_page_call", 8000)

        self._classify_cache: dict[str, tuple[str, float]] = {}
        self._compose_cache: dict[str, str] = {}
        self._page_type_cache: dict[str, str] = {}
        self._scope_cache: dict[str, str] = {}

        # Phase 7 — batch-API queue. When ``cost_saver_mode`` is on,
        # ``compose_description`` enqueues instead of calling the API.
        # The Assembler calls :meth:`flush_pending_compose` once per run.
        self._cost_saver = bool(config.get("cost_saver_mode", False))
        self._pending_compose: dict[str, dict] = {}   # cache_key → request dict

    # ── Core call ──────────────────────────────────────────────────────────

    def _call(
        self,
        model: str,
        system: str,
        messages: list,
        max_tokens: int | None = None,
    ) -> str:
        resp = self._client.messages.create(
            model=model,
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
        self._tracker.record(resp.usage, model)
        return resp.content[0].text

    def _vision_call(
        self,
        model: str,
        system: str,
        image_bytes: bytes,
        prompt: str,
        max_tokens: int | None = None,
    ) -> str:
        b64 = base64.standard_b64encode(image_bytes).decode()
        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens or self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
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
        self._tracker.record(resp.usage, model)
        return resp.content[0].text

    # ── Tier 3: Haiku — classification ─────────────────────────────────────

    def classify_page_type(self, text: str) -> str:
        snippet = (text or "")[:600]
        if not snippet.strip():
            return "PLAN_CONSTRUCTION"
        if snippet in self._page_type_cache:
            return self._page_type_cache[snippet]
        try:
            raw = self._call(
                self._haiku,
                _SYSTEM_PAGE_TYPE,
                [{"role": "user", "content": snippet}],
                max_tokens=24,
            )
            cls = raw.strip().upper().split()[0]
        except Exception:
            cls = "PLAN_CONSTRUCTION"
        self._page_type_cache[snippet] = cls
        return cls

    def classify_scope_vs_reference(self, note_text: str) -> str:
        key = note_text[:400]
        if key in self._scope_cache:
            return self._scope_cache[key]
        try:
            raw = self._call(
                self._haiku,
                _SYSTEM_SCOPE_VS_REFERENCE,
                [{"role": "user", "content": note_text}],
                max_tokens=16,
            )
            result = raw.strip().lower()
        except Exception:
            result = "scope"
        self._scope_cache[key] = result
        return result

    def classify_csi(self, description: str, fallback_keywords: dict) -> tuple[str, float]:
        """Kept for backward compat; CSI grouping is dropped post-Step-11."""
        key = description.lower().strip()
        if key in self._classify_cache:
            return self._classify_cache[key]
        try:
            raw = self._call(
                self._haiku,
                _SYSTEM_CSI_CLASSIFY,
                [{"role": "user", "content": f"Classify this construction item:\n{description}"}],
                max_tokens=64,
            )
            parsed = json.loads(raw)
            division = parsed.get("division", "")
            confidence = float(parsed.get("confidence", 0.7))
        except Exception:
            division, confidence = _keyword_classify(description, fallback_keywords), 0.5
        self._classify_cache[key] = (division, confidence)
        return division, confidence

    # ── Tier 4: Sonnet — composition + vision crops ────────────────────────

    def compose_description(self, raw: str, sheet: str = "", keynote_ref: str = "") -> str:
        from ai.description_composer import _SYSTEM
        raw = (raw or "").strip()
        cache_key = f"{raw}|{sheet}|{keynote_ref}"
        if cache_key in self._compose_cache:
            return self._compose_cache[cache_key]
        user_content = f"Sheet: {sheet}\nKeynote: {keynote_ref}\nRaw: {raw}"
        # Phase 7: in cost-saver mode we enqueue here and return a sentinel
        # uppercase fallback. ``flush_pending_compose`` resolves the queue
        # in one batched API call after all pages are processed and back-
        # fills the cache; ``Assembler`` then re-runs ``compose`` to pick
        # up the real result.
        if self._cost_saver:
            self._pending_compose.setdefault(cache_key, {
                "raw": raw,
                "sheet": sheet,
                "keynote_ref": keynote_ref,
                "system": _SYSTEM,
                "user": user_content,
            })
            return raw.upper()
        try:
            result = self._call(
                self._sonnet,
                _SYSTEM,
                [{"role": "user", "content": user_content}],
                max_tokens=512,
            ).strip()
        except Exception:
            result = raw.upper()
        self._compose_cache[cache_key] = result
        return result

    @property
    def cost_saver_mode(self) -> bool:
        return self._cost_saver

    @property
    def pending_compose_count(self) -> int:
        return len(self._pending_compose)

    def flush_pending_compose(
        self,
        on_progress=None,
    ) -> int:
        """Run every queued ``compose_description`` through the Batches API.

        Returns the number of compose results that were filled in. Safe
        to call when nothing is queued (returns 0). On any batch failure
        we silently fall back to the synchronous path so callers always
        get a reply — just not at the discounted rate.
        """
        from ai.batch_runner import BatchRequest, BatchRunner
        if not self._pending_compose:
            return 0

        pending = list(self._pending_compose.items())
        self._pending_compose.clear()

        requests = [
            BatchRequest(
                custom_id=key,
                model=self._sonnet,
                system=req["system"],
                messages=[{"role": "user", "content": req["user"]}],
                max_tokens=512,
            )
            for key, req in pending
        ]

        runner = BatchRunner(self._client)
        results = runner.run(
            requests,
            on_progress=on_progress,
            record_usage=lambda u, m: self._tracker.record_batch(u, m or self._sonnet),
        )

        filled = 0
        for key, req in pending:
            text = (results.get(key) or "").strip()
            if text:
                self._compose_cache[key] = text
                filled += 1
            else:
                # Fall back to the standard sync path so the row still
                # gets a real description (and contributes to the
                # non-discounted bucket).
                try:
                    sync_result = self._call(
                        self._sonnet,
                        req["system"],
                        [{"role": "user", "content": req["user"]}],
                        max_tokens=512,
                    ).strip()
                except Exception:
                    sync_result = req["raw"].upper()
                self._compose_cache[key] = sync_result
        return filled

    def extract_legend_from_image(self, image_bytes: bytes, prompt: str) -> str:
        return self._vision_call(self._sonnet, _SYSTEM_EXTRACTION, image_bytes, prompt)

    def extract_title_block_vision(self, image_bytes: bytes, prompt: str) -> str:
        return self._vision_call(self._sonnet, _SYSTEM_EXTRACTION, image_bytes, prompt)

    def extract_schedule_from_image(self, image_bytes: bytes, prompt: str) -> str:
        return self._vision_call(self._sonnet, _SYSTEM_EXTRACTION, image_bytes, prompt)

    # Backward-compat alias used by older parser code.
    def interpret_image_region(self, image_bytes: bytes, prompt: str) -> str:
        return self._vision_call(self._sonnet, _SYSTEM_EXTRACTION, image_bytes, prompt)

    # ── Tier 4 escalation: Opus — explicit `claude_only` mode ──────────────

    def extract_full_page_vision(self, image_bytes: bytes) -> list[dict]:
        prompt = (
            "Extract all construction work items from this architectural drawing sheet. "
            "Look for: keynote tables, general notes, scope notes, schedules, count tables. "
            "For each item return a JSON object with: "
            '{"id": str, "description": str, "qty": number, "units": str, "table_type": "A"|"C"|"D"}. '
            "Use EA for each, LS for lump sum, SF for square feet, LF for linear feet. "
            "Return ONLY a JSON array. No preamble, no markdown fences."
        )
        try:
            raw = self._vision_call(self._opus, _SYSTEM_EXTRACTION, image_bytes, prompt)
            return json.loads(raw)
        except Exception:
            return []

    # Older callsites use this name.
    def extract_page_claude_only(self, image_bytes: bytes) -> list[dict]:
        return self.extract_full_page_vision(image_bytes)

    # ── Phase 6: chat over the row table ───────────────────────────────────

    def chat_over_rows(
        self,
        rows_payload: list[dict],
        history: list[tuple[str, str]],
        question: str,
        max_tokens: int = 700,
    ) -> str:
        """Sonnet 4.6 chat over the takeoff row table.

        ``history`` is a list of ``(role, content)`` strings replayed in
        order. Both the system prompt AND the row table are wrapped in
        an ephemeral cache block so back-to-back questions on the same
        takeoff pay full price once and 10% thereafter (cache-hit).
        """
        from ai.chat_agent import _SYSTEM_CHAT
        rows_json = json.dumps(rows_payload, separators=(",", ":"))
        # The row table is its own user content block so the cache key
        # only flips when the takeoff actually changes.
        messages: list[dict] = []
        for role, content in history:
            if role not in ("user", "assistant"):
                continue
            messages.append({"role": role, "content": content})
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"ROW_TABLE = {rows_json}",
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": f"Question: {question}"},
            ],
        })
        resp = self._client.messages.create(
            model=self._sonnet,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_CHAT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
        self._tracker.record(resp.usage, self._sonnet)
        return resp.content[0].text

    # ── Phase 5: set diff descriptions ─────────────────────────────────────

    def describe_diff_cluster(
        self,
        old_png: bytes,
        new_png: bytes,
        *,
        sheet_id: str = "",
    ) -> str:
        """Sonnet, two-image: describe what changed between OLD and NEW crops.

        Returns at most one sentence. The system prompt is cache-tagged so
        a full set-diff run pays for the system prompt once.
        """
        if not old_png or not new_png:
            return ""
        prompt = (
            f"You are reviewing one specific region of sheet {sheet_id or '(unknown)'}. "
            "The first image is the OLD revision, the second is the NEW revision of the "
            "same region (already aligned to the same coordinate frame). "
            "Describe what changed in ONE concise sentence. "
            "If nothing meaningful changed (just anti-alias jitter, line weight, scan noise), "
            'reply exactly: "no meaningful change".'
        )
        old_b64 = base64.standard_b64encode(old_png).decode()
        new_b64 = base64.standard_b64encode(new_png).decode()
        try:
            resp = self._client.messages.create(
                model=self._sonnet,
                max_tokens=160,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_DIFF,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {
                                "type": "base64", "media_type": "image/png", "data": old_b64,
                            }},
                            {"type": "image", "source": {
                                "type": "base64", "media_type": "image/png", "data": new_b64,
                            }},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            self._tracker.record(resp.usage, self._sonnet)
            return resp.content[0].text.strip()
        except Exception:
            return ""


def _keyword_classify(description: str, keywords: dict) -> str:
    lower = description.lower()
    for division, kws in keywords.items():
        if any(kw in lower for kw in kws):
            return division
    return "DIVISION 09"
