"""Vision extraction agent — image -> raw JSON-ish text.

Routing: configured ``provider`` first; on ``ProviderCapabilityError``
(NVIDIA's vision is gated to the maverick model) fall back to
``fallback_provider`` + ``fallback_model``. Caller parses the returned text.
"""
from __future__ import annotations

from ai.agents import AgentContext
from ai.providers.base import ProviderCapabilityError


_SYSTEM_EXTRACTION = """You are a construction document parser for Quantity Takeoff (QTO) extraction.
You analyze architectural and engineering drawing sheets and extract structured data.

Output ONLY valid JSON — no markdown fences, no preamble, no explanation.
All string values must be properly escaped JSON strings."""


def extract_from_image(image_bytes: bytes, prompt: str, ctx: AgentContext) -> str:
    """Run a vision call against the configured provider.

    Args:
        image_bytes: Raw PNG bytes (already cropped/rendered upstream).
        prompt: Per-call extraction prompt (e.g. legend vs. schedule shape).
        ctx: Agent context. Reads ``provider``, ``model``,
            ``fallback_provider``, ``fallback_model``, ``max_tokens`` from
            ``ctx.agent_config``.

    Returns:
        Raw assistant text. Caller is responsible for ``json.loads``. On
        any non-capability error returns ``""``.
    """
    primary_name = ctx.agent_config.get("provider", "anthropic")
    primary_model = ctx.agent_config.get("model", "")
    max_tokens = int(ctx.agent_config.get("max_tokens", 4000))
    primary = ctx.providers.get(primary_name)

    fallback_name = ctx.agent_config.get("fallback_provider")
    fallback_model = ctx.agent_config.get("fallback_model", "")
    fallback = ctx.providers.get(fallback_name) if fallback_name else None

    # Capability pre-check: skip the round trip if the primary can't even
    # advertise vision, or its vision is model-gated to a model we don't
    # have configured.
    use_primary = primary is not None and getattr(primary, "supports_vision", False)

    if use_primary:
        try:
            return primary.vision(
                primary_model,
                _SYSTEM_EXTRACTION,
                image_bytes,
                prompt,
                max_tokens,
                cache_system=getattr(primary, "supports_caching", False),
            )
        except ProviderCapabilityError:
            # NVIDIA vision is gated to a single model — fall through.
            pass
        except Exception:
            return ""

    if fallback is not None:
        try:
            return fallback.vision(
                fallback_model,
                _SYSTEM_EXTRACTION,
                image_bytes,
                prompt,
                max_tokens,
                cache_system=getattr(fallback, "supports_caching", False),
            )
        except Exception:
            return ""

    return ""
