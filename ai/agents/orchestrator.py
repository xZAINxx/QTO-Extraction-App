"""Orchestrator review agent — Sonnet (or configured) pass over low-confidence rows.

Mirrors :meth:`ai.client.AIClient.review_low_confidence_rows` exactly, but
routes through ``AgentContext.providers`` so the multi-agent path can swap
the reviewer model independently of the rest of the pipeline.

Behavior parity (tested in ``tests/test_review_rows.py`` for AIClient):
* Filter: non-header rows below ``threshold``.
* Chunking: groups of 20 rows per call.
* Verdicts: ``confirm`` bumps confidence to 0.9 + clears ``needs_review``;
  ``revise`` updates description + sets ``extraction_method = "reviewed"``;
  ``reject`` is a no-op so the validator can still flag the row.
* Errors are swallowed per chunk so one bad chunk doesn't kill the rest.
"""
from __future__ import annotations

import json

from ai.agents import AgentContext


_SYSTEM_REVIEW = (
    "You are reviewing low-confidence rows from an automated construction QTO extraction. "
    "For each row in the input JSON array, assess whether the description, quantity, and units "
    "look correct given the sheet context and extraction method. "
    "Output ONLY a JSON array. Each element: "
    '{"row_id": <int>, "verdict": "confirm"|"revise"|"reject", "revised_description": "<text, only when revise>"}. '
    "Use 'confirm' if the row looks valid; 'revise' if the description should be improved (provide revised_description); "
    "'reject' if the row appears spurious. Return one element per input row, in any order. "
    "No markdown, no preamble, no explanation."
)


def review_rows(rows: list, threshold: float, ctx: AgentContext) -> int:
    """Send low-confidence rows to the configured orchestrator provider for review.

    Mirrors :meth:`AIClient.review_low_confidence_rows` behavior but routes
    through the :class:`AgentContext` ``providers`` map. Returns the number
    of rows whose verdict was applied (``confirm`` + ``revise``). Rejected
    rows are left unchanged.

    Args:
        rows: Full list of :class:`QTORow` instances. Mutated in place.
        threshold: Confidence cut-off — rows strictly below this are
            candidates for review.
        ctx: Agent context. Reads ``provider`` (default ``"anthropic"``),
            ``model``, and ``max_tokens`` (default ``1500``) from
            ``ctx.agent_config``.

    Returns:
        Count of rows whose description / confidence was updated.
    """
    provider_name = ctx.agent_config.get("provider", "anthropic")
    provider = ctx.providers.get(provider_name)
    if provider is None:
        return 0

    # Index-preserving filter: ``row_id`` is the original list index so
    # in-place updates apply to the right row regardless of any reordering
    # the orchestrator performs in its response.
    low_conf = [
        (idx, row)
        for idx, row in enumerate(rows)
        if not row.is_header_row and row.confidence < threshold
    ]
    if not low_conf:
        return 0

    model = ctx.agent_config.get("model", "")
    max_tokens = int(ctx.agent_config.get("max_tokens", 1500))

    chunk_size = 20
    applied = 0
    for start in range(0, len(low_conf), chunk_size):
        chunk = low_conf[start:start + chunk_size]
        payload = [
            {
                "row_id": idx,
                "description": row.description,
                "qty": row.qty,
                "units": row.units,
                "sheet": row.source_sheet,
                "method": row.extraction_method,
                "confidence": row.confidence,
            }
            for idx, row in chunk
        ]
        try:
            raw = provider.chat(
                model,
                _SYSTEM_REVIEW,
                [{"role": "user", "content": json.dumps(payload, separators=(",", ":"))}],
                max_tokens,
                cache_system=getattr(provider, "supports_caching", False),
            )
            verdicts = json.loads(raw)
        except Exception:
            continue
        if not isinstance(verdicts, list):
            continue
        for verdict in verdicts:
            if not isinstance(verdict, dict):
                continue
            row_id = verdict.get("row_id")
            if not isinstance(row_id, int) or row_id < 0 or row_id >= len(rows):
                continue
            kind = verdict.get("verdict")
            target = rows[row_id]
            if kind == "confirm":
                target.confidence = 0.9
                target.needs_review = False
                applied += 1
            elif kind == "revise":
                revised = verdict.get("revised_description")
                if isinstance(revised, str) and revised.strip():
                    target.description = revised
                target.confidence = 0.9
                target.needs_review = False
                target.extraction_method = "reviewed"
                applied += 1
            # "reject" → leave row unchanged
    return applied
