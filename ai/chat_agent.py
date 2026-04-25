"""Phase-6: natural-language chat over the extracted QTO row table.

Sends the row table as compact JSON to Sonnet 4.6 alongside a heavily
cached system prompt. The model is instructed to answer construction
questions (e.g. "how many windows on south elevation?") with a short
narrative answer plus a JSON ``citations`` array pointing at the rows
it relied on. The UI uses those citations to jump to the correct
sheet/page in the embedded PDF viewer.

Token strategy:
- System prompt (~1k tokens) is wrapped in
  ``cache_control: {"type":"ephemeral"}`` so the second question pays
  10% of the prompt cost.
- The row table is passed as the user's first text block, also
  cache-tagged. As long as the row set hasn't changed between
  questions, the table is read from cache too. This keeps a 5-question
  session well below $0.01.
- We cap the number of rows we ship in the prompt at ``_MAX_ROWS``; if
  the takeoff is bigger we drop low-value columns first (math_trail,
  trade_division) before falling back to truncation. The fallback warns
  via ``ChatAnswer.notes``.
- Conversation history is kept short (last 4 turns) and stripped of any
  citations objects to keep the assistant transcript tiny.

The heavy logic lives here, the UI in :mod:`ui.chat_panel` is a thin
view layer.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

from core.qto_row import QTORow


_LOG = logging.getLogger(__name__)
_MAX_ROWS = 400          # truncation guard for huge takeoffs
_HISTORY_TURNS = 4       # how many prior Q/A pairs we replay
_MAX_TOKENS = 700        # response budget — answers + small JSON


_SYSTEM_CHAT = """You are the in-app assistant for the Zeconic Quantity Takeoff (QTO) tool.

You answer questions about a construction takeoff. The user passes you the
current row table as compact JSON. Each row has: row_id, sheet, page,
description, qty, units, details, trade.

Rules:
1. Answer in ≤ 3 short paragraphs, then one bullet list if it helps.
2. NEVER invent rows. Numbers must be sums of qty values from rows you cite.
3. Always end your reply with a fenced ```json``` block containing a
   ``citations`` array. Each citation is an object with at least
   ``row_id`` (int), ``sheet`` (string), and ``page`` (int).
4. If the question can't be answered from the table, say so explicitly
   and return an empty citations array.
5. Sheet IDs come from the row data — do NOT normalize or reformat them.
6. Quantities are summed in the units shown. Do not convert units.
7. Keep tone professional and terse, like a senior estimator.

Example answer block:

The south elevation (sheet A-201) shows 6 windows total, all type W2.

```json
{"citations":[{"row_id":42,"sheet":"A-201","page":25},
              {"row_id":43,"sheet":"A-201","page":25}]}
```
"""


@dataclass
class Citation:
    row_id: int
    sheet: str = ""
    page: int = 0


@dataclass
class ChatAnswer:
    """A single assistant turn's response."""
    text: str
    citations: list[Citation] = field(default_factory=list)
    notes: str = ""    # internal warnings (truncation, parse failure, etc.)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass
class _Turn:
    role: str   # "user" | "assistant"
    content: str


def _serialize_rows(rows: Iterable[QTORow]) -> tuple[list[dict], bool]:
    """Compact JSON-friendly view of the row table.

    Returns ``(payload, truncated)``. Header rows are dropped. We also
    strip rows with empty description AND zero qty — they carry no
    answerable content and just bloat the prompt.
    """
    payload: list[dict] = []
    for idx, row in enumerate(rows):
        if getattr(row, "is_header_row", False):
            continue
        desc = (row.description or "").strip()
        qty = float(row.qty or 0)
        if not desc and qty == 0:
            continue
        payload.append({
            "row_id": idx,
            "sheet": row.source_sheet or row.drawings or "",
            "page": int(row.source_page or 0),
            "description": desc,
            "qty": qty,
            "units": row.units or "",
            "details": (row.details or "").strip(),
            "trade": row.trade_division or "",
        })
    truncated = len(payload) > _MAX_ROWS
    if truncated:
        payload = payload[:_MAX_ROWS]
    return payload, truncated


def _parse_answer(raw: str) -> ChatAnswer:
    """Split the model output into prose + citations JSON.

    Tolerates the JSON block being missing or malformed — citations is
    just empty in that case so the UI still shows the prose answer.
    """
    text = raw.strip()
    citations: list[Citation] = []
    fence_open = text.rfind("```json")
    fence_close = text.rfind("```")
    if fence_open != -1 and fence_close > fence_open:
        json_blob = text[fence_open + len("```json"): fence_close].strip()
        prose = (text[:fence_open] + text[fence_close + 3:]).strip()
        try:
            parsed = json.loads(json_blob)
            for c in parsed.get("citations", []) or []:
                citations.append(Citation(
                    row_id=int(c.get("row_id", 0)),
                    sheet=str(c.get("sheet", "")),
                    page=int(c.get("page", 0)),
                ))
        except Exception as exc:
            _LOG.debug("citations parse failed: %s — %s", exc, json_blob[:120])
            prose = text
    else:
        prose = text
    return ChatAnswer(text=prose, citations=citations)


class ChatAgent:
    """Stateful chat session over a fixed row table.

    Call :meth:`set_rows` whenever the underlying takeoff changes — that
    invalidates the cached row payload so the next question reflects the
    latest data.
    """

    def __init__(self, ai_client):
        self._ai = ai_client
        self._rows_payload: list[dict] = []
        self._rows_truncated = False
        self._row_signature: int = 0
        self._history: list[_Turn] = []

    # ── State management ─────────────────────────────────────────────────

    def set_rows(self, rows: list[QTORow]) -> None:
        payload, truncated = _serialize_rows(rows)
        sig = hash(tuple(
            (r["row_id"], r["sheet"], r["page"], r["qty"], r["description"])
            for r in payload
        ))
        if sig == self._row_signature and self._rows_payload:
            return
        self._rows_payload = payload
        self._rows_truncated = truncated
        self._row_signature = sig
        # Row table changed → past conversation references stale data.
        self._history.clear()

    def reset_history(self) -> None:
        self._history.clear()

    @property
    def has_rows(self) -> bool:
        return bool(self._rows_payload)

    # ── Ask ──────────────────────────────────────────────────────────────

    def ask(self, question: str) -> ChatAnswer:
        question = (question or "").strip()
        if not question:
            return ChatAnswer(text="(empty question)")
        if not self.has_rows:
            return ChatAnswer(
                text="No takeoff rows are loaded yet — run an extraction first."
            )
        if self._ai is None or not hasattr(self._ai, "chat_over_rows"):
            return ChatAnswer(
                text="Chat is unavailable: no AI client wired up.",
                notes="missing chat_over_rows on AIClient",
            )

        try:
            raw = self._ai.chat_over_rows(
                rows_payload=self._rows_payload,
                history=[(t.role, t.content) for t in self._history[-2 * _HISTORY_TURNS:]],
                question=question,
                max_tokens=_MAX_TOKENS,
            )
        except Exception as exc:
            _LOG.warning("chat_over_rows failed: %s", exc)
            return ChatAnswer(
                text=f"AI request failed: {exc}",
                notes=str(exc),
            )

        answer = _parse_answer(raw)
        if self._rows_truncated:
            answer.notes = (
                f"Row table truncated to {_MAX_ROWS} rows — answer may miss "
                "items. Filter the takeoff to scope the question."
            )

        self._history.append(_Turn(role="user", content=question))
        # Strip the citations fence from history so we don't waste tokens
        # replaying it; citations are turn-local UI metadata.
        self._history.append(_Turn(role="assistant", content=answer.text))
        return answer
