"""Multi-agent dispatcher with the same public surface as :class:`AIClient`.

This is the parallel implementation the assembler talks to in
``extraction_mode == "multi_agent"``. Each public method is a thin delegate
to a function-based agent in :mod:`ai.agents`, plus a couple of cases where
we lazily fall back to :class:`AIClient` (chat over rows + diff cluster
description) because their prompts are tuned for Sonnet's prompt-caching
shape and routing them through the provider abstraction would lose that.

Behavioral parity with :class:`AIClient`:

* Same per-key compose cache (``compose_description`` is the hot path).
* Same Phase-7 batch-shim *attributes* so :meth:`Assembler.flush_batched_compose`
  stays a clean ``getattr`` consumer; in multi-agent mode the shims are
  no-ops (no batch API yet).
* ``review_low_confidence_rows`` exists too — the Assembler invokes it
  unconditionally now (not gated on ``cost_saver_mode``).
"""
from __future__ import annotations

import json
from typing import Optional

from core.token_tracker import TokenTracker


class MultiAgentClient:
    """Same public surface as :class:`AIClient`; dispatches to ``ai.agents.*``.

    The constructor instantiates both providers and keeps a small set of
    per-process caches that mirror the ones :class:`AIClient` uses for
    classification and composition. Provider routing per-agent comes from
    ``config["agents"][<agent_name>]``.
    """

    def __init__(self, config: dict, tracker: TokenTracker):
        from ai.providers.anthropic_provider import AnthropicProvider
        from ai.providers.nvidia_provider import NvidiaProvider
        from core.rag_store import HistoricalStore

        self._config = config
        self._tracker = tracker
        self._providers = {
            "anthropic": AnthropicProvider(config, tracker),
            "nvidia": NvidiaProvider(config, tracker),
        }
        self._agents_cfg: dict = config.get("agents", {})
        self._rag_cfg: dict = config.get("rag", {})
        self._rag = HistoricalStore(self._rag_cfg) if self._rag_cfg.get("enabled") else None

        # Match AIClient's caches for behavioral parity.
        self._classify_cache: dict = {}
        self._compose_cache: dict = {}
        self._page_type_cache: dict = {}
        self._scope_cache: dict = {}

        # Phase-7 batch shims (no-ops in multi_agent mode).
        self._cost_saver = False
        self._pending_compose: dict = {}

        # Lazy AIClient fallback for the two methods whose Sonnet-tuned
        # prompt-caching shape doesn't translate cleanly through providers.
        self._anthropic_fallback: Optional["AIClient"] = None  # noqa: F821

    # ── Internal helpers ─────────────────────────────────────────────────

    def _ctx(self, agent_name: str):
        """Build an :class:`AgentContext` for ``agent_name``."""
        from ai.agents import AgentContext
        return AgentContext(
            providers=self._providers,
            tracker=self._tracker,
            agent_config=self._agents_cfg.get(agent_name, {}),
            cache=None,
            rag_store=self._rag,
        )

    def _fallback(self):
        """Lazily build (and reuse) an :class:`AIClient` for chat / diff."""
        if self._anthropic_fallback is None:
            from ai.client import AIClient
            self._anthropic_fallback = AIClient(self._config, self._tracker)
        return self._anthropic_fallback

    # ── Tier 3 — classification ──────────────────────────────────────────

    def classify_page_type(self, text: str) -> str:
        from ai.agents.page_classifier import classify_page
        return classify_page(text, self._ctx("page_classifier"))

    def classify_scope_vs_reference(self, note_text: str) -> str:
        # No dedicated agent yet — keep this on Anthropic via the fallback
        # so we don't have to duplicate the prompt in two places.
        return self._fallback().classify_scope_vs_reference(note_text)

    def classify_csi(self, description: str, fallback_keywords: dict) -> tuple[str, float]:
        from ai.agents.csi_classifier import classify
        return classify(description, fallback_keywords, self._ctx("csi_classifier"))

    # ── Tier 4 — composition + vision ────────────────────────────────────

    def compose_description(self, raw: str, sheet: str = "", keynote_ref: str = "") -> str:
        from ai.agents.description_normalizer import normalize
        raw = (raw or "").strip()
        cache_key = f"{raw}|{sheet}|{keynote_ref}"
        if cache_key in self._compose_cache:
            return self._compose_cache[cache_key]
        result = normalize(raw, sheet, keynote_ref, self._ctx("normalizer"))
        self._compose_cache[cache_key] = result
        return result

    def extract_legend_from_image(self, image_bytes: bytes, prompt: str) -> str:
        from ai.agents.vision_extractor import extract_from_image
        return extract_from_image(image_bytes, prompt, self._ctx("vision_extractor"))

    def extract_title_block_vision(self, image_bytes: bytes, prompt: str) -> str:
        from ai.agents.vision_extractor import extract_from_image
        return extract_from_image(image_bytes, prompt, self._ctx("vision_extractor"))

    def extract_schedule_from_image(self, image_bytes: bytes, prompt: str) -> str:
        from ai.agents.vision_extractor import extract_from_image
        return extract_from_image(image_bytes, prompt, self._ctx("vision_extractor"))

    def interpret_image_region(self, image_bytes: bytes, prompt: str) -> str:
        # Backward-compat alias used by older parser code.
        from ai.agents.vision_extractor import extract_from_image
        return extract_from_image(image_bytes, prompt, self._ctx("vision_extractor"))

    def extract_full_page_vision(self, image_bytes: bytes) -> list[dict]:
        from ai.agents.vision_extractor import extract_from_image
        prompt = (
            "Extract all construction work items from this architectural drawing sheet. "
            "Look for: keynote tables, general notes, scope notes, schedules, count tables. "
            "For each item return a JSON object with: "
            '{"id": str, "description": str, "qty": number, "units": str, "table_type": "A"|"C"|"D"}. '
            "Use EA for each, LS for lump sum, SF for square feet, LF for linear feet. "
            "Return ONLY a JSON array. No preamble, no markdown fences."
        )
        try:
            raw = extract_from_image(image_bytes, prompt, self._ctx("vision_extractor"))
            return json.loads(raw)
        except Exception:
            return []

    def extract_page_claude_only(self, image_bytes: bytes) -> list[dict]:
        # Backward-compat alias.
        return self.extract_full_page_vision(image_bytes)

    # ── Phase 6 — chat over rows (delegated) ─────────────────────────────

    def chat_over_rows(
        self,
        rows_payload: list[dict],
        history: list[tuple[str, str]],
        question: str,
        max_tokens: int = 700,
    ) -> str:
        # Delegated to AIClient because the Sonnet prompt-caching shape on
        # this call (cache the row table, replay history) doesn't translate
        # cleanly through the provider abstraction.
        return self._fallback().chat_over_rows(rows_payload, history, question, max_tokens)

    # ── Phase 5 — diff cluster description (delegated) ───────────────────

    def describe_diff_cluster(
        self,
        old_png: bytes,
        new_png: bytes,
        *,
        sheet_id: str = "",
    ) -> str:
        # Sonnet diff prompt is tuned for the Anthropic two-image call —
        # delegate so we don't duplicate the prompt + cache wiring.
        return self._fallback().describe_diff_cluster(old_png, new_png, sheet_id=sheet_id)

    # ── Phase 7 — batch shims (no-ops in multi_agent mode) ───────────────

    @property
    def cost_saver_mode(self) -> bool:
        return False

    @property
    def pending_compose_count(self) -> int:
        return 0

    def flush_pending_compose(self, on_progress=None) -> int:
        return 0

    # ── Phase 8 — orchestrator review ────────────────────────────────────

    def review_low_confidence_rows(self, rows, threshold: float = 0.75) -> int:
        from ai.agents.orchestrator import review_rows
        return review_rows(rows, threshold, self._ctx("orchestrator"))
