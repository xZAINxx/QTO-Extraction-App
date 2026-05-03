"""Provider abstraction for the multi-agent QTO refactor.

Exposes a single :class:`Provider` Protocol with capability flags so the
agent layer can route by capability without a symmetric ABC. Two concrete
implementations are shipped in this package:

* :class:`AnthropicProvider` — caching, batches, vision (no embeddings/rerank)
* :class:`NvidiaProvider`   — embeddings, rerank, OpenAI-compatible chat,
  optional vision via ``meta/llama-4-maverick-17b-128e-instruct``
"""
from ai.providers.base import Provider, ProviderCapabilityError
from ai.providers.anthropic_provider import AnthropicProvider
from ai.providers.nvidia_provider import NvidiaProvider

__all__ = [
    "Provider",
    "ProviderCapabilityError",
    "AnthropicProvider",
    "NvidiaProvider",
]
