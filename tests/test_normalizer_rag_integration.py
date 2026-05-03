"""Tests for the RAG-priming branch in ``ai.agents.description_normalizer``.

The normalizer's standard path is covered in ``test_agents.py``. These
tests focus exclusively on the new ``use_rag_priming`` branch:

* When True, ``prime_normalizer`` is called and its output is spliced
  into the system prompt.
* When False, the system prompt is the bare ``_SYSTEM`` (no RAG block).
* Empty examples must not produce a stray empty RAG header.

We patch ``ai.agents.rag.prime_normalizer`` (not the symbol re-imported
inside ``description_normalizer.normalize``) — the lazy ``from`` re-resolves
the attribute on each call, so the patch takes effect.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from ai.agents import AgentContext
from ai.agents.description_normalizer import normalize as normalize_description
from ai.description_composer import _SYSTEM as _COMPOSER_SYSTEM


# ── Fakes ────────────────────────────────────────────────────────────────


class FakeProvider:
    """Records call args; returns a scripted chat response."""

    name = "nvidia"
    supports_caching = False
    supports_batches = False
    supports_vision = False
    supports_embeddings = True
    supports_reranking = True

    def __init__(self, chat_response: str = "NORMALIZED OUTPUT"):
        self.chat_response = chat_response
        self.chat_calls: list[dict] = []

    def chat(self, model, system, messages, max_tokens, *,
             cache_system=False, temperature=None):
        self.chat_calls.append({
            "model": model, "system": system, "messages": messages,
            "max_tokens": max_tokens, "cache_system": cache_system,
            "temperature": temperature,
        })
        return self.chat_response


def _ctx(provider: FakeProvider, *, agent_config: dict | None = None,
         rag_store: Any = None) -> AgentContext:
    return AgentContext(
        providers={provider.name: provider},
        tracker=SimpleNamespace(record=lambda *a, **k: None),  # type: ignore[arg-type]
        agent_config=agent_config or {},
        rag_store=rag_store,
    )


# ── Tests ────────────────────────────────────────────────────────────────


def test_normalizer_uses_rag_when_use_rag_priming_true():
    """Provider must be called with a system that contains the RAG header
    *and* the historical Raw/Normalized pairs returned by the mocked
    ``prime_normalizer``."""
    provider = FakeProvider(chat_response="PROVIDE & INSTALL X @ Y")
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia",
        "model": "nemotron-mini-4b",
        "max_tokens": 512,
        "use_rag_priming": True,
    }, rag_store=object())  # truthy sentinel; prime_normalizer is mocked.

    fake_examples = [
        {"raw_input": "paint exterior", "normalized": "PRIME & PAINT EXTERIOR"},
        {"raw_input": "patch concrete", "normalized": "PATCH & REPAIR CONCRETE"},
    ]
    with patch("ai.agents.rag.prime_normalizer", return_value=fake_examples):
        normalize_description("foo", "A-101", "1/A101", ctx)

    assert len(provider.chat_calls) == 1
    system = provider.chat_calls[0]["system"]
    # Original prompt must remain intact (verbatim prefix).
    assert system.startswith(_COMPOSER_SYSTEM)
    # RAG header + both example bodies must appear.
    assert "ADDITIONAL HISTORICAL EXAMPLES" in system
    assert "Raw: paint exterior" in system
    assert "Normalized: PRIME & PAINT EXTERIOR" in system
    assert "Raw: patch concrete" in system
    assert "Normalized: PATCH & REPAIR CONCRETE" in system


def test_normalizer_skips_rag_when_use_rag_priming_false():
    """``use_rag_priming`` defaulting to False (or set False) must call
    the provider with the bare ``_SYSTEM`` — no RAG header at all, and
    ``prime_normalizer`` must not even be invoked."""
    provider = FakeProvider(chat_response="OUT")
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia",
        "model": "nemotron-mini-4b",
        "max_tokens": 512,
        # explicit False, but absence behaves identically.
        "use_rag_priming": False,
    }, rag_store=object())

    with patch("ai.agents.rag.prime_normalizer") as mock_prime:
        normalize_description("foo", "A-101", "1/A101", ctx)
        mock_prime.assert_not_called()

    system = provider.chat_calls[0]["system"]
    assert system == _COMPOSER_SYSTEM
    assert "ADDITIONAL HISTORICAL EXAMPLES" not in system


def test_normalizer_handles_empty_rag_examples():
    """``prime_normalizer`` returning ``[]`` must not append the RAG header
    (no empty section in the prompt)."""
    provider = FakeProvider(chat_response="OUT")
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia",
        "model": "nemotron-mini-4b",
        "max_tokens": 512,
        "use_rag_priming": True,
    }, rag_store=object())

    with patch("ai.agents.rag.prime_normalizer", return_value=[]):
        normalize_description("foo", "A-101", "1/A101", ctx)

    system = provider.chat_calls[0]["system"]
    assert system == _COMPOSER_SYSTEM
    assert "ADDITIONAL HISTORICAL EXAMPLES" not in system
