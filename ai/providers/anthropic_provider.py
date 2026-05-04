"""Anthropic provider — wraps the SDK calls already living in ``ai/client.py``.

This class mirrors :meth:`ai.client.AIClient._call` and
:meth:`ai.client.AIClient._vision_call` exactly, including the
``cache_control: {"type": "ephemeral"}`` system wrapper when
``cache_system=True``. The point is not new behavior; it is a tiny adapter
that lets the new agent layer call Anthropic via the same ``Provider``
surface as NVIDIA NIM.

Embeddings and reranking are unsupported and raise
:class:`ProviderCapabilityError` so callers can fall through to NVIDIA.
"""
from __future__ import annotations

import base64
import os
from typing import Any

import anthropic

from ai.providers.base import Provider, ProviderCapabilityError
from core.token_tracker import TokenTracker


class AnthropicProvider:
    """Provider implementation backed by ``anthropic.Anthropic()``."""

    name: str = "anthropic"
    supports_caching: bool = True
    supports_batches: bool = True
    supports_vision: bool = True
    supports_embeddings: bool = False
    supports_reranking: bool = False

    def __init__(self, config: dict, tracker: TokenTracker):
        api_key = (
            config.get("anthropic_api_key")
            or os.environ.get("ANTHROPIC_API_KEY", "")
        )
        self._client = anthropic.Anthropic(api_key=api_key)
        self._tracker = tracker

    # ── Chat ──────────────────────────────────────────────────────────────

    def chat(
        self,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        *,
        cache_system: bool = False,
        temperature: float | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": _system_block(system, cache_system),
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = self._client.messages.create(**kwargs)
        self._tracker.record(resp.usage, model)
        return resp.content[0].text

    # ── Vision ────────────────────────────────────────────────────────────

    def vision(
        self,
        model: str,
        system: str,
        image_bytes: bytes,
        prompt: str,
        max_tokens: int,
        *,
        cache_system: bool = False,
    ) -> str:
        b64 = base64.standard_b64encode(image_bytes).decode()
        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_system_block(system, cache_system),
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

    # ── Capability-gated stubs ────────────────────────────────────────────

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise ProviderCapabilityError(
            "AnthropicProvider does not support embeddings; use NvidiaProvider."
        )

    def rerank(
        self,
        model: str,
        query: str,
        passages: list[str],
    ) -> list[tuple[int, float]]:
        raise ProviderCapabilityError(
            "AnthropicProvider does not support reranking; use NvidiaProvider."
        )


# ── Helpers ───────────────────────────────────────────────────────────────


def _system_block(system: str, cache_system: bool):
    """Build the ``system=`` argument with optional ephemeral cache control.

    Mirrors the shape used in :meth:`ai.client.AIClient._call` so any tests
    or callers that asserted on the exact structure keep working.
    """
    if cache_system:
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    return system
