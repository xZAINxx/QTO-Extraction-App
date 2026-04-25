"""Phase-6 chat agent tests.

Locks in the parts that *don't* require a live API key:

* row serialisation drops headers and empty rows
* citation parsing tolerates malformed JSON
* :meth:`set_rows` resets history on a real change but not on a no-op
* :meth:`ask` propagates the response from a stub ``AIClient`` and
  surfaces truncation warnings
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai.chat_agent import (
    ChatAgent, ChatAnswer, Citation, _parse_answer, _serialize_rows,
)
from core.qto_row import QTORow


def _row(**kw) -> QTORow:
    base = dict(
        s_no=0, drawings="", details="", description="x", qty=1.0,
        units="EA", source_page=1, source_sheet="A-101",
    )
    base.update(kw)
    return QTORow(**base)


def test_serialize_rows_drops_headers_and_empty():
    rows = [
        _row(is_header_row=True, description="HEADER"),
        _row(description="real item", qty=4),
        _row(description="", qty=0),                # empty: dropped
        _row(description="", qty=2),                # empty desc but has qty: kept
        _row(description="another", qty=1, source_sheet="A-102", source_page=3),
    ]
    payload, truncated = _serialize_rows(rows)
    assert truncated is False
    descriptions = [p["description"] for p in payload]
    assert "HEADER" not in descriptions
    assert "real item" in descriptions
    assert "another" in descriptions
    assert payload[-1]["sheet"] == "A-102"
    assert payload[-1]["page"] == 3


def test_parse_answer_tolerates_missing_json_block():
    raw = "Total walls = 6.\n\nI am sure."
    ans = _parse_answer(raw)
    assert "Total walls" in ans.text
    assert ans.citations == []


def test_parse_answer_extracts_citations():
    raw = (
        "There are 3 doors on A-101.\n\n"
        "```json\n"
        '{"citations":[{"row_id":4,"sheet":"A-101","page":12},'
        '{"row_id":7,"sheet":"A-101","page":12}]}\n'
        "```"
    )
    ans = _parse_answer(raw)
    assert "3 doors" in ans.text
    assert "```" not in ans.text
    assert len(ans.citations) == 2
    assert ans.citations[0] == Citation(row_id=4, sheet="A-101", page=12)


def test_parse_answer_handles_malformed_json_gracefully():
    raw = "Answer.\n\n```json\n{not valid}\n```"
    ans = _parse_answer(raw)
    assert ans.citations == []
    # We keep the original text so the user still sees something useful.
    assert "Answer" in ans.text


def test_set_rows_resets_history_on_change():
    rows1 = [_row(description="a")]
    rows2 = [_row(description="b")]
    stub = SimpleNamespace(
        chat_over_rows=lambda **kw: 'OK\n\n```json\n{"citations":[]}\n```',
    )
    agent = ChatAgent(stub)
    agent.set_rows(rows1)
    agent.ask("first?")
    assert len(agent._history) == 2  # user + assistant

    agent.set_rows(rows1)
    assert len(agent._history) == 2  # unchanged → preserved

    agent.set_rows(rows2)
    assert agent._history == []      # different rows → cleared


def test_ask_returns_no_rows_message_when_table_empty():
    agent = ChatAgent(ai_client=SimpleNamespace(chat_over_rows=lambda **kw: ""))
    answer = agent.ask("anything?")
    assert "No takeoff" in answer.text


def test_ask_propagates_truncation_warning(monkeypatch):
    monkeypatch.setattr("ai.chat_agent._MAX_ROWS", 2)
    rows = [_row(description=f"item {i}") for i in range(5)]
    stub = SimpleNamespace(
        chat_over_rows=lambda **kw: 'short\n\n```json\n{"citations":[]}\n```',
    )
    agent = ChatAgent(stub)
    agent.set_rows(rows)
    answer = agent.ask("how many?")
    assert "truncated" in answer.notes.lower()
    assert answer.text.strip() == "short"


def test_ask_handles_ai_failure():
    def boom(**kw):
        raise RuntimeError("network down")

    stub = SimpleNamespace(chat_over_rows=boom)
    agent = ChatAgent(stub)
    agent.set_rows([_row(description="x")])
    answer = agent.ask("anything?")
    assert "network down" in answer.text


def test_ask_returns_friendly_message_when_ai_lacks_method():
    agent = ChatAgent(ai_client=object())  # no chat_over_rows attr
    agent.set_rows([_row(description="x")])
    answer = agent.ask("hello?")
    assert "Chat is unavailable" in answer.text


def test_chat_answer_is_empty_property():
    assert ChatAnswer(text="").is_empty
    assert not ChatAnswer(text="hi").is_empty
