"""CSI MasterFormat classification agent.

Mirrors :meth:`ai.client.AIClient.classify_csi`: try the LLM first, then
fall back to keyword matching at low confidence (0.5). Kept as a real,
called surface even though :mod:`ai.client` flagged the original method as
"backward compat only post-Step-11" — this is the live entry point in the
multi-agent path.
"""
from __future__ import annotations

import json

from ai.agents import AgentContext


_SYSTEM_CSI_CLASSIFY = """You are a construction specification classifier.
Classify descriptions into CSI MasterFormat divisions.
Output ONLY a JSON object with key "division" (e.g. "DIVISION 02") and "confidence" (0.0-1.0).
No markdown, no preamble."""


def classify(
    description: str,
    fallback_keywords: dict,
    ctx: AgentContext,
) -> tuple[str, float]:
    """Classify a description into a CSI division with a confidence.

    Args:
        description: Free-form description (typically a single QTO row).
        fallback_keywords: ``{division: [keywords...]}`` map used when the
            LLM call fails. Mirrors the contract of
            :meth:`AIClient.classify_csi`.
        ctx: Agent context.

    Returns:
        ``(division, confidence)``. Division is a string like
        ``"DIVISION 09"``; confidence is in ``[0.0, 1.0]``. On any error
        falls back to keyword classification at confidence 0.5.
    """
    provider_name = ctx.agent_config.get("provider", "anthropic")
    provider = ctx.providers.get(provider_name)

    if provider is not None:
        model = ctx.agent_config.get("model", "")
        max_tokens = int(ctx.agent_config.get("max_tokens", 64))
        temperature = ctx.agent_config.get("temperature")
        try:
            raw = provider.chat(
                model,
                _SYSTEM_CSI_CLASSIFY,
                [{
                    "role": "user",
                    "content": f"Classify this construction item:\n{description}",
                }],
                max_tokens,
                temperature=temperature,
            )
            parsed = json.loads(raw)
            division = parsed.get("division", "")
            confidence = float(parsed.get("confidence", 0.7))
            if division:
                return division, confidence
        except Exception:
            pass

    return _keyword_classify(description, fallback_keywords), 0.5


def _keyword_classify(description: str, keywords: dict) -> str:
    """Mirror of :func:`ai.client._keyword_classify`."""
    lower = (description or "").lower()
    for division, kws in keywords.items():
        if any(kw in lower for kw in kws):
            return division
    return "DIVISION 09"
