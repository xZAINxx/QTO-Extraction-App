"""RAG priming agent — embed raw text → retrieve historical examples.

Given a raw keynote string and an :class:`AgentContext` with a populated
``rag_store``, return the top-K historical ``(raw_input, normalized)`` pairs
that the description normalizer can splice into its few-shot prompt as
"additional historical examples."

Design constraints (from the plan, section 7):

* **Failure must never break extraction.** The entire body is wrapped in a
  single ``try/except Exception`` that returns ``[]`` on any unhandled
  failure — RAG is opportunistic, not load-bearing.
* **Two failure boundaries.** Embedding failures fall through to the outer
  except (return ``[]``), but rerank failures fall back to cosine-only
  ranking — losing the rerank quality boost is preferable to losing the
  retrieval entirely.
* **Capability-gated.** If no NVIDIA provider is wired in, or its
  ``supports_embeddings`` flag is False, return ``[]`` without attempting
  the call (avoids ``ProviderCapabilityError`` spam in logs).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.agents import AgentContext


# Defaults for the embedding + rerank models. Overridable per-agent via
# ``ctx.agent_config``; keeping them in one place avoids string drift.
_DEFAULT_EMBED_MODEL = "nvidia/nv-embed-v1"
_DEFAULT_RERANK_MODEL = "nv-rerank-qa-mistral-4b:1"
_DEFAULT_TOP_K = 5
# Fixed pool size for the cosine search before rerank narrows it. 20 is a
# good ratio against the typical top_k=5 — rerank gets meaningful headroom
# without scanning the whole table.
_SEARCH_POOL_SIZE = 20


def prime_normalizer(raw: str, ctx: "AgentContext") -> list[dict]:
    """Embed ``raw``, search the historical store, rerank, return top-K rows.

    Args:
        raw: Raw keynote text for the row currently being normalized.
        ctx: Shared agent context. Reads ``ctx.rag_store``,
            ``ctx.providers["nvidia"]`` (with ``supports_embeddings``), and
            ``ctx.agent_config`` for ``rag_top_k`` /
            ``rag_embedding_model`` / ``rag_rerank_model`` overrides.

    Returns:
        List of row dicts (each containing at least ``raw_input`` and
        ``normalized``) ordered best-first. Returns ``[]`` if RAG is
        disabled, the store is empty, the NVIDIA provider is missing or
        lacks embeddings, or any unexpected failure occurs.
    """
    try:
        if ctx.rag_store is None:
            return []

        nvidia = ctx.providers.get("nvidia")
        if nvidia is None or not getattr(nvidia, "supports_embeddings", False):
            return []

        embedding_model = ctx.agent_config.get(
            "rag_embedding_model", _DEFAULT_EMBED_MODEL
        )
        rerank_model = ctx.agent_config.get(
            "rag_rerank_model", _DEFAULT_RERANK_MODEL
        )
        top_k = int(ctx.agent_config.get("rag_top_k", _DEFAULT_TOP_K))

        # Embed query. Wrong-length unpacking would raise ValueError, which
        # the outer except catches — that's the safe behavior.
        [q_emb] = nvidia.embed(embedding_model, [raw])

        candidates = ctx.rag_store.search(q_emb, top_k=_SEARCH_POOL_SIZE)
        if not candidates:
            return []

        # Inner failure boundary: rerank is a quality booster, not a
        # correctness gate. If it dies, the cosine ordering from search()
        # is already sorted desc — just take the head and move on.
        passages = [row["raw_input"] for _, row in candidates]
        try:
            pairs = nvidia.rerank(rerank_model, raw, passages)
            return [candidates[idx][1] for idx, _ in pairs[:top_k]]
        except Exception:
            return [row for _, row in candidates[:top_k]]
    except Exception:
        # Hard outer guard: RAG must never propagate a failure into the
        # extraction pipeline. Empty list = caller proceeds without priming.
        return []


__all__ = ["prime_normalizer"]
