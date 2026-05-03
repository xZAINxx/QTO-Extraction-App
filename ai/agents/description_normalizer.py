"""Description normalizer agent — raw keynote -> GC-format description.

Reuses ``ai.description_composer._SYSTEM`` verbatim. That prompt is
model-portable (the 13 few-shots carry the format constraints), so the
agent's only job is to swap *who* answers the call.

When ``ctx.agent_config["use_rag_priming"]`` is True, this agent calls
:func:`ai.agents.rag.prime_normalizer` to fetch top-K historical
``(raw, normalized)`` pairs from the SQLite store and appends them to the
system prompt as additional few-shots. Disabled by default; failure of the
RAG path silently degrades to the standard prompt rather than failing the
extraction.

Failure mode for the LLM call matches
:meth:`ai.client.AIClient.compose_description`: return ``raw.upper()`` so
callers never get an empty cell.
"""
from __future__ import annotations

from ai.agents import AgentContext
from ai.description_composer import _SYSTEM


def _format_rag_examples(examples: list[dict]) -> str:
    """Render historical rows as a few-shot block appended to ``_SYSTEM``.

    Empty input returns an empty string so the caller can unconditionally
    concatenate without branching on length.
    """
    if not examples:
        return ""
    lines = ["\n\n# ADDITIONAL HISTORICAL EXAMPLES (from prior approved takeoffs):"]
    for ex in examples:
        lines.append(f"\nRaw: {ex['raw_input']}\nNormalized: {ex['normalized']}")
    return "\n".join(lines)


def normalize(
    raw: str,
    sheet: str,
    keynote_ref: str,
    ctx: AgentContext,
) -> str:
    """Normalize a raw keynote into a GC-format description.

    Args:
        raw: Raw keynote text (or scope-note text) for one row.
        sheet: Sheet identifier (e.g. ``"A-102"``) — used in user content.
        keynote_ref: Detail/legend cross-reference (e.g.
            ``"1/A901 & LEGEND/A102"``).
        ctx: Agent context. When ``ctx.agent_config["use_rag_priming"]``
            is True, the system prompt is augmented with retrieved
            historical examples; otherwise the bare ``_SYSTEM`` is used.

    Returns:
        Composed description string. On error returns ``raw.upper()`` to
        match :meth:`AIClient.compose_description`.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""

    provider_name = ctx.agent_config.get("provider", "anthropic")
    provider = ctx.providers.get(provider_name)
    if provider is None:
        return raw.upper()

    model = ctx.agent_config.get("model", "")
    max_tokens = int(ctx.agent_config.get("max_tokens", 512))
    temperature = ctx.agent_config.get("temperature")

    # RAG priming is opt-in. The lazy import here keeps the rag module out
    # of the import graph for callers that never enable it, and lets tests
    # patch ``ai.agents.rag.prime_normalizer`` — each call re-resolves the
    # attribute on the rag module so the patch takes effect.
    system_prompt = _SYSTEM
    if ctx.agent_config.get("use_rag_priming", False):
        from ai.agents.rag import prime_normalizer
        examples = prime_normalizer(raw, ctx)
        system_prompt = _SYSTEM + _format_rag_examples(examples)

    user_content = f"Sheet: {sheet}\nKeynote: {keynote_ref}\nRaw: {raw}"
    try:
        result = provider.chat(
            model,
            system_prompt,
            [{"role": "user", "content": user_content}],
            max_tokens,
            cache_system=getattr(provider, "supports_caching", False),
            temperature=temperature,
        )
        return (result or "").strip() or raw.upper()
    except Exception:
        return raw.upper()
