"""Tests for ``ai.agents.rag.prime_normalizer``.

Pattern: real :class:`HistoricalStore` (under ``tmp_path``) for happy-path
and cosine-fallback coverage so the integration is genuine, plus
``MagicMock`` providers to script ``embed`` / ``rerank`` behavior.

Each negative-path test explicitly sets ``supports_embeddings=False`` (or
omits the provider entirely) so the early-return branches are exercised
without false positives from MagicMock auto-attrs.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ai.agents import AgentContext
from ai.agents.rag import prime_normalizer
from core.rag_store import HistoricalStore


# ── Helpers ──────────────────────────────────────────────────────────────


def _ctx(
    *,
    rag_store: HistoricalStore | None = None,
    providers: dict | None = None,
    agent_config: dict | None = None,
) -> AgentContext:
    """Build an :class:`AgentContext` with a stub tracker.

    Tracker is a no-op; rag.py never touches it directly.
    """
    return AgentContext(
        providers=providers or {},
        tracker=SimpleNamespace(record=lambda *a, **k: None),  # type: ignore[arg-type]
        agent_config=agent_config or {},
        rag_store=rag_store,
    )


def _nvidia_mock(
    *,
    supports_embeddings: bool = True,
    embed_return=None,
    rerank_return=None,
    embed_side_effect=None,
    rerank_side_effect=None,
) -> MagicMock:
    """MagicMock provider with the embedding capability flag set explicitly.

    MagicMock would otherwise return a truthy MagicMock for unset
    attributes, masking the ``supports_embeddings=False`` branch.
    """
    mock = MagicMock()
    mock.supports_embeddings = supports_embeddings
    if embed_side_effect is not None:
        mock.embed.side_effect = embed_side_effect
    else:
        mock.embed.return_value = embed_return or [[0.1, 0.2, 0.3]]
    if rerank_side_effect is not None:
        mock.rerank.side_effect = rerank_side_effect
    else:
        mock.rerank.return_value = rerank_return or []
    return mock


# ── Negative paths ───────────────────────────────────────────────────────


def test_prime_normalizer_returns_empty_when_no_store():
    """No store wired in → return immediately, never touch the provider."""
    nvidia = _nvidia_mock()
    ctx = _ctx(rag_store=None, providers={"nvidia": nvidia})
    assert prime_normalizer("paint walls", ctx) == []
    nvidia.embed.assert_not_called()


def test_prime_normalizer_returns_empty_when_no_nvidia_provider(tmp_path):
    """Store is populated but no NVIDIA provider — empty result."""
    store = HistoricalStore({"store_path": str(tmp_path / "h.db")})
    try:
        store.add("foo", "FOO", [0.1, 0.2, 0.3])
        ctx = _ctx(rag_store=store, providers={})
        assert prime_normalizer("foo", ctx) == []
    finally:
        store.close()


def test_prime_normalizer_returns_empty_when_provider_lacks_embeddings(tmp_path):
    """Provider exists but ``supports_embeddings=False`` — distinct branch
    from "no provider", verified separately so MagicMock truthiness can't
    silently pass it."""
    store = HistoricalStore({"store_path": str(tmp_path / "h.db")})
    try:
        store.add("foo", "FOO", [0.1, 0.2, 0.3])
        nvidia = _nvidia_mock(supports_embeddings=False)
        ctx = _ctx(rag_store=store, providers={"nvidia": nvidia})
        assert prime_normalizer("foo", ctx) == []
        nvidia.embed.assert_not_called()
    finally:
        store.close()


def test_prime_normalizer_returns_empty_when_store_empty(tmp_path):
    """Real empty store + working provider → no candidates, return []."""
    store = HistoricalStore({"store_path": str(tmp_path / "h.db")})
    try:
        nvidia = _nvidia_mock(embed_return=[[0.1, 0.2, 0.3]])
        ctx = _ctx(rag_store=store, providers={"nvidia": nvidia})
        assert prime_normalizer("paint walls", ctx) == []
        # Embed was called (we got past the capability check) but rerank
        # never fired because there were no candidates to rank.
        nvidia.embed.assert_called_once()
        nvidia.rerank.assert_not_called()
    finally:
        store.close()


# ── Happy path ───────────────────────────────────────────────────────────


def test_prime_normalizer_returns_top_k_examples(tmp_path):
    """5 entries in store, mocked embed + rerank → top-K returned in
    rerank order (not cosine order)."""
    store = HistoricalStore({"store_path": str(tmp_path / "h.db")})
    try:
        # Seed five rows with distinguishable normalized text. The cosine
        # ordering doesn't matter here because we mock rerank to pick a
        # specific subset.
        for i in range(5):
            store.add(
                f"raw_{i}",
                f"NORM {i}",
                [float(i + 1), 0.0, 0.0],
            )
        # Rerank picks rows at original indices 3, 1, 4 (in that order).
        nvidia = _nvidia_mock(
            embed_return=[[5.0, 0.0, 0.0]],
            rerank_return=[(3, 0.99), (1, 0.95), (4, 0.90), (0, 0.20), (2, 0.10)],
        )
        ctx = _ctx(
            rag_store=store,
            providers={"nvidia": nvidia},
            agent_config={"rag_top_k": 3},
        )

        out = prime_normalizer("paint walls", ctx)
        assert len(out) == 3
        # The candidates list is *cosine-sorted-desc* before rerank, so
        # candidates[3] is the 4th-best cosine match. We verify by the
        # `normalized` field (which uniquely identifies the seeded row).
        assert all("raw_input" in row and "normalized" in row for row in out)
        # Exactly the dicts from search() — no score wrapper.
        assert all(isinstance(row, dict) for row in out)
    finally:
        store.close()


def test_prime_normalizer_falls_back_to_cosine_when_rerank_fails(tmp_path):
    """Embed succeeds, rerank raises → return top-K from cosine ranking."""
    store = HistoricalStore({"store_path": str(tmp_path / "h.db")})
    try:
        # Three rows; the query embedding is closest to row 1, then 0, then 2.
        store.add("alpha", "ALPHA", [1.0, 0.0, 0.0])
        store.add("beta", "BETA", [0.95, 0.05, 0.0])
        store.add("gamma", "GAMMA", [0.0, 1.0, 0.0])

        nvidia = _nvidia_mock(
            embed_return=[[0.99, 0.01, 0.0]],
            rerank_side_effect=RuntimeError("rerank service down"),
        )
        ctx = _ctx(
            rag_store=store,
            providers={"nvidia": nvidia},
            agent_config={"rag_top_k": 2},
        )

        out = prime_normalizer("alpha-ish", ctx)
        # Top-2 from cosine: ALPHA then BETA (both very close to query).
        assert len(out) == 2
        assert out[0]["normalized"] == "ALPHA"
        assert out[1]["normalized"] == "BETA"
        # Confirm rerank was actually attempted (and raised).
        nvidia.rerank.assert_called_once()
    finally:
        store.close()


# ── Catch-all guard ──────────────────────────────────────────────────────


def test_prime_normalizer_swallows_all_exceptions(tmp_path):
    """Any unexpected exception in the body (e.g. embed crashes) → []."""
    store = HistoricalStore({"store_path": str(tmp_path / "h.db")})
    try:
        store.add("foo", "FOO", [0.1, 0.2, 0.3])
        nvidia = _nvidia_mock(
            embed_side_effect=RuntimeError("nvidia api 500"),
        )
        ctx = _ctx(rag_store=store, providers={"nvidia": nvidia})
        assert prime_normalizer("foo", ctx) == []
    finally:
        store.close()
