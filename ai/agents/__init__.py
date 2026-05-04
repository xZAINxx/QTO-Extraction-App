"""Function-based agents for the multi-agent extraction path.

Each agent is a single module with a single public function. They share a
common :class:`AgentContext` so callers (``MultiAgentClient``) can wire up
provider routing, the token tracker, and optional caching/RAG once and pass
it through unchanged.

Design notes:

* Agents are stateless. Any per-run caching belongs to ``MultiAgentClient``
  so the agent itself stays trivially testable with a ``FakeProvider``.
* ``agent_config`` is *this agent's slice* of the config (e.g.
  ``config["agents"]["page_classifier"]``), not the full config dict —
  this keeps each agent free of routing logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ai.providers.base import Provider
    from core.token_tracker import TokenTracker
    from core.cache import ResultCache
    from core.rag_store import HistoricalStore


@dataclass
class AgentContext:
    """Shared dependencies passed to every agent function.

    Attributes:
        providers: Map of provider name -> Provider instance. Agents look
            up their primary/fallback providers by name from
            ``agent_config["provider"]`` / ``agent_config["fallback_provider"]``.
        tracker: Token tracker for cost accounting. Providers update it
            internally; agents do not need to call it directly.
        agent_config: This agent's slice of ``config["agents"][<name>]``.
            Carries ``provider``, ``model``, ``max_tokens``, ``temperature``,
            and any agent-specific knobs (e.g. ``fast_path_heuristics``).
        cache: Optional result cache. Reserved for ``MultiAgentClient``
            wrappers; agents themselves never read it directly.
        rag_store: Optional historical-description store. Wired in commit 4
            for the description normalizer's RAG priming path.
    """

    providers: dict[str, "Provider"]
    tracker: "TokenTracker"
    agent_config: dict
    cache: Optional["ResultCache"] = None
    rag_store: Optional["HistoricalStore"] = None


__all__ = ["AgentContext"]
