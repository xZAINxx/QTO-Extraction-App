"""Page-type classification agent.

Strategy: heuristics first (fast, free), LLM second (only when the heuristic
returns the default ``PLAN_CONSTRUCTION`` fallback). Mirrors the
``_SYSTEM_PAGE_TYPE`` prompt from :mod:`ai.client` so output stays in the
fixed enum the rest of the pipeline already handles.
"""
from __future__ import annotations

from ai.agents import AgentContext


_SYSTEM_PAGE_TYPE = (
    "You classify a single architectural drawing sheet by page type. "
    "Output ONLY one of: PLAN_DEMO, PLAN_CONSTRUCTION, ELEVATION, SCHEDULE, "
    "DETAIL_WITH_SCOPE, DETAIL, LEGEND_ONLY, TITLE_PAGE, ALLOWANCES_PROVISIONS. "
    "No preamble, no JSON."
)

_DEFAULT = "PLAN_CONSTRUCTION"
_VALID_TYPES = {
    "PLAN_DEMO", "PLAN_CONSTRUCTION", "ELEVATION", "SCHEDULE",
    "DETAIL_WITH_SCOPE", "DETAIL", "LEGEND_ONLY", "TITLE_PAGE",
    "ALLOWANCES_PROVISIONS",
}


def classify_page(text: str, ctx: AgentContext) -> str:
    """Classify a page by its extracted text.

    Args:
        text: Raw page text from PyMuPDF.
        ctx: Agent context. Reads ``provider``, ``model``, ``max_tokens``,
            ``temperature``, and ``fast_path_heuristics`` from
            ``ctx.agent_config``.

    Returns:
        One of the nine canonical page-type tokens. Falls back to
        ``"PLAN_CONSTRUCTION"`` on any error or unknown LLM output.
    """
    snippet = (text or "")[:600]
    if not snippet.strip():
        return _DEFAULT

    # Fast path — pure-text heuristic. Skip only if explicitly disabled.
    if ctx.agent_config.get("fast_path_heuristics", True):
        from parser.pdf_splitter import classify_page as heuristic_classify
        info = heuristic_classify(0, text)
        if info.page_type != _DEFAULT:
            return info.page_type
        # Heuristic fell through to default — escalate to the LLM below.

    provider_name = ctx.agent_config.get("provider", "anthropic")
    provider = ctx.providers.get(provider_name)
    if provider is None:
        return _DEFAULT

    model = ctx.agent_config.get("model", "")
    max_tokens = int(ctx.agent_config.get("max_tokens", 24))
    temperature = ctx.agent_config.get("temperature")

    try:
        raw = provider.chat(
            model,
            _SYSTEM_PAGE_TYPE,
            [{"role": "user", "content": snippet}],
            max_tokens,
            temperature=temperature,
        )
    except Exception:
        return _DEFAULT

    cls = (raw or "").strip().upper().split()[0] if (raw or "").strip() else _DEFAULT
    return cls if cls in _VALID_TYPES else _DEFAULT
