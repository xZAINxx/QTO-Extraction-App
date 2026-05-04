"""Provider Protocol + capability error.

The Protocol intentionally mixes "always-supported" methods (``chat``) with
capability-gated ones (``vision``, ``embed``, ``rerank``). Each concrete
implementation must declare its capability flags so callers can branch
without try/except scaffolding for routine routing decisions.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class ProviderCapabilityError(RuntimeError):
    """Raised when a provider is asked for a capability it does not support.

    Example: ``AnthropicProvider.embed(...)`` raises this so the caller can
    fall through to a provider whose ``supports_embeddings`` flag is True.
    """


@runtime_checkable
class Provider(Protocol):
    """Unified inference surface across Anthropic + NVIDIA NIM.

    Capability flags drive routing — callers should consult them before
    invoking a method that may raise :class:`ProviderCapabilityError`.
    """

    name: str
    supports_caching: bool
    supports_batches: bool
    supports_vision: bool
    supports_embeddings: bool
    supports_reranking: bool

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
        """Single-turn chat completion. Returns the assistant text only."""

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
        """Single-image vision call. ``image_bytes`` is raw PNG bytes."""

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one float vector per input."""

    def rerank(
        self,
        model: str,
        query: str,
        passages: list[str],
    ) -> list[tuple[int, float]]:
        """Rerank ``passages`` against ``query``.

        Returns ``[(original_index, score), ...]`` sorted by score desc.
        """
