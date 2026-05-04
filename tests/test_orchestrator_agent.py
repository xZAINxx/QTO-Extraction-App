"""Tests for ai.agents.orchestrator.review_rows.

Mirrors the contract verified for ``AIClient.review_low_confidence_rows``
in ``tests/test_review_rows.py`` — but routes through the function-based
agent + provider abstraction. We use a tiny ``FakeProvider`` that captures
``chat()`` invocations so we can assert chunk sizes and verify the
verdict-application semantics in isolation.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from ai.agents import AgentContext
from ai.agents.orchestrator import review_rows
from core.qto_row import QTORow


# ── Fakes ────────────────────────────────────────────────────────────────


class FakeProvider:
    """Minimal Provider stub that returns scripted responses to chat()."""

    def __init__(
        self,
        name: str = "anthropic",
        *,
        chat_response: Any = "[]",
        supports_caching: bool = True,
    ):
        self.name = name
        self.supports_caching = supports_caching
        self.supports_batches = False
        self.supports_vision = False
        self.supports_embeddings = False
        self.supports_reranking = False
        self.chat_response = chat_response
        self.chat_calls: list[dict] = []

    def chat(self, model, system, messages, max_tokens, *, cache_system=False, temperature=None):
        self.chat_calls.append({
            "model": model, "system": system, "messages": messages,
            "max_tokens": max_tokens, "cache_system": cache_system,
        })
        if isinstance(self.chat_response, BaseException):
            raise self.chat_response
        if callable(self.chat_response):
            return self.chat_response(messages)
        return self.chat_response


def _ctx(provider: FakeProvider, *, model: str = "claude-sonnet-4-6") -> AgentContext:
    tracker = SimpleNamespace(record=lambda *a, **k: None)
    return AgentContext(
        providers={provider.name: provider},
        tracker=tracker,  # type: ignore[arg-type]
        agent_config={"provider": provider.name, "model": model, "max_tokens": 1500},
    )


def _row(
    description: str = "demo cmu wall",
    qty: float = 10.0,
    units: str = "SF",
    sheet: str = "A-101",
    method: str = "vector",
    confidence: float = 0.6,
    is_header: bool = False,
) -> QTORow:
    return QTORow(
        description=description,
        qty=qty,
        units=units,
        source_sheet=sheet,
        extraction_method=method,
        confidence=confidence,
        is_header_row=is_header,
        needs_review=confidence < 0.75,
    )


# ── Tests ────────────────────────────────────────────────────────────────


def test_orchestrator_review_rows_chunks_by_20():
    """50 low-confidence rows → 3 chunks (20, 20, 10)."""
    chunk_sizes: list[int] = []

    def _chat(messages):
        payload = json.loads(messages[0]["content"])
        chunk_sizes.append(len(payload))
        return json.dumps([{"row_id": item["row_id"], "verdict": "confirm"} for item in payload])

    provider = FakeProvider(chat_response=_chat)
    rows = [_row(confidence=0.5) for _ in range(50)]
    applied = review_rows(rows, threshold=0.75, ctx=_ctx(provider))
    assert applied == 50
    assert chunk_sizes == [20, 20, 10]
    assert len(provider.chat_calls) == 3


def test_orchestrator_review_rows_applies_confirm():
    """Confirm verdict bumps confidence to 0.9 + clears needs_review; keeps method."""
    rows = [_row(confidence=0.4)]
    payload = json.dumps([{"row_id": 0, "verdict": "confirm"}])
    provider = FakeProvider(chat_response=payload)
    applied = review_rows(rows, threshold=0.75, ctx=_ctx(provider))
    assert applied == 1
    assert rows[0].confidence == 0.9
    assert rows[0].needs_review is False
    assert rows[0].extraction_method == "vector"  # unchanged
    assert rows[0].description == "demo cmu wall"  # unchanged


def test_orchestrator_review_rows_applies_revise():
    """Revise updates description, sets method='reviewed', confidence to 0.9."""
    rows = [_row(description="cmu wall", confidence=0.4)]
    payload = json.dumps([{
        "row_id": 0,
        "verdict": "revise",
        "revised_description": "FURNISH AND INSTALL 8\" CMU WALL PER A-101",
    }])
    provider = FakeProvider(chat_response=payload)
    applied = review_rows(rows, threshold=0.75, ctx=_ctx(provider))
    assert applied == 1
    assert rows[0].description == "FURNISH AND INSTALL 8\" CMU WALL PER A-101"
    assert rows[0].extraction_method == "reviewed"
    assert rows[0].confidence == 0.9
    assert rows[0].needs_review is False


def test_orchestrator_review_rows_skips_reject():
    """Reject leaves the row entirely unchanged (validator picks it up later)."""
    rows = [_row(description="weird item", confidence=0.4)]
    payload = json.dumps([{"row_id": 0, "verdict": "reject"}])
    provider = FakeProvider(chat_response=payload)
    applied = review_rows(rows, threshold=0.75, ctx=_ctx(provider))
    assert applied == 0
    assert rows[0].description == "weird item"
    assert rows[0].confidence == 0.4
    assert rows[0].extraction_method == "vector"


def test_orchestrator_review_rows_handles_provider_error():
    """One chunk's RuntimeError must not poison the next chunk."""
    chunk_index = {"i": 0}

    def _chat(messages):
        chunk_index["i"] += 1
        if chunk_index["i"] == 1:
            raise RuntimeError("boom")
        payload = json.loads(messages[0]["content"])
        return json.dumps([{"row_id": item["row_id"], "verdict": "confirm"} for item in payload])

    provider = FakeProvider(chat_response=_chat)
    # 30 low-conf rows → 2 chunks (20 + 10). First chunk raises; second succeeds.
    rows = [_row(confidence=0.5) for _ in range(30)]
    applied = review_rows(rows, threshold=0.75, ctx=_ctx(provider))
    # Only the second chunk's 10 rows applied — first chunk swallowed silently.
    assert applied == 10
    # First-chunk rows untouched; second-chunk rows confirmed.
    assert rows[0].confidence == 0.5
    assert rows[20].confidence == 0.9


def test_orchestrator_review_rows_no_low_conf_returns_zero():
    """All rows at/above threshold → no provider call, returns 0."""
    rows = [_row(confidence=0.9), _row(confidence=0.95), _row(confidence=0.8)]
    provider = FakeProvider(chat_response="[]")
    applied = review_rows(rows, threshold=0.75, ctx=_ctx(provider))
    assert applied == 0
    assert provider.chat_calls == []


def test_orchestrator_review_rows_skips_header_rows():
    """is_header_row rows are excluded from review even at low confidence."""
    rows = [
        _row(confidence=0.5, is_header=True),
        _row(confidence=0.5, is_header=False),
    ]
    payload = json.dumps([{"row_id": 1, "verdict": "confirm"}])
    provider = FakeProvider(chat_response=payload)
    applied = review_rows(rows, threshold=0.75, ctx=_ctx(provider))
    assert applied == 1
    sent_payload = json.loads(provider.chat_calls[0]["messages"][0]["content"])
    assert len(sent_payload) == 1
    assert sent_payload[0]["row_id"] == 1


def test_orchestrator_review_rows_no_provider_returns_zero():
    """Missing provider in ctx → graceful zero, no exception."""
    tracker = SimpleNamespace(record=lambda *a, **k: None)
    ctx = AgentContext(
        providers={},  # no providers
        tracker=tracker,  # type: ignore[arg-type]
        agent_config={"provider": "anthropic", "model": "x", "max_tokens": 1500},
    )
    rows = [_row(confidence=0.5)]
    applied = review_rows(rows, threshold=0.75, ctx=ctx)
    assert applied == 0
