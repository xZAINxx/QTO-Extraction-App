"""Text extraction agent — raw page text -> list of typed dicts.

Used for pages where the upstream geometry/table layer already produced
clean text (no need to spend a vision call). The agent's only job is to
coerce free-form text into a JSON array of row dicts.
"""
from __future__ import annotations

import json

from ai.agents import AgentContext


_SYSTEM_EXTRACTION = """You are a construction document parser for Quantity Takeoff (QTO) extraction.
You analyze architectural and engineering drawing sheets and extract structured data.

Output ONLY valid JSON — no markdown fences, no preamble, no explanation.
All string values must be properly escaped JSON strings."""


def extract_from_text(text: str, prompt: str, ctx: AgentContext) -> list[dict]:
    """Extract a JSON array of work-item dicts from raw page text.

    Args:
        text: Raw page text (may include keynote tables, scope notes, etc.).
        prompt: Per-call extraction prompt describing the target schema.
        ctx: Agent context. Reads ``provider``, ``model``, ``max_tokens``,
            ``temperature`` from ``ctx.agent_config``.

    Returns:
        Parsed list of dicts (each at minimum contains ``id``,
        ``description``, ``qty``, ``units``). Returns ``[]`` on any
        provider error or invalid JSON.
    """
    provider_name = ctx.agent_config.get("provider", "anthropic")
    provider = ctx.providers.get(provider_name)
    if provider is None:
        return []

    model = ctx.agent_config.get("model", "")
    max_tokens = int(ctx.agent_config.get("max_tokens", 2000))
    temperature = ctx.agent_config.get("temperature")

    user_content = f"{prompt}\n\nRaw page text:\n{text}"
    try:
        raw = provider.chat(
            model,
            _SYSTEM_EXTRACTION,
            [{"role": "user", "content": user_content}],
            max_tokens,
            temperature=temperature,
        )
    except Exception:
        return []

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]
