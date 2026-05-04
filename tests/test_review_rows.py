"""Tests for AIClient.review_low_confidence_rows."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from ai.client import AIClient
from core.qto_row import QTORow
from core.token_tracker import TokenTracker


def _make_row(
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


def _client() -> AIClient:
    return AIClient({"anthropic_api_key": "test"}, TokenTracker())


# ── 1. No low-confidence rows ─────────────────────────────────────────────

def test_review_low_confidence_rows_skips_when_no_low_conf_rows():
    client = _client()
    rows = [
        _make_row(confidence=0.9),
        _make_row(confidence=0.95),
        _make_row(confidence=0.8),
    ]
    with patch.object(client, "_call") as mock_call:
        n = client.review_low_confidence_rows(rows, threshold=0.75)
    assert n == 0
    mock_call.assert_not_called()
    # All rows untouched.
    assert all(r.confidence in (0.9, 0.95, 0.8) for r in rows)


# ── 2. Confirm verdict ────────────────────────────────────────────────────

def test_review_low_confidence_rows_applies_confirm_verdict():
    client = _client()
    rows = [_make_row(confidence=0.5)]
    payload = json.dumps([{"row_id": 0, "verdict": "confirm"}])
    with patch.object(client, "_call", return_value=payload):
        n = client.review_low_confidence_rows(rows, threshold=0.75)
    assert n == 1
    assert rows[0].confidence == 0.9
    assert rows[0].needs_review is False
    # Confirm must NOT change extraction_method or description.
    assert rows[0].extraction_method == "vector"
    assert rows[0].description == "demo cmu wall"


# ── 3. Revise verdict ─────────────────────────────────────────────────────

def test_review_low_confidence_rows_applies_revise_verdict():
    client = _client()
    rows = [_make_row(description="cmu wall", confidence=0.4)]
    payload = json.dumps([{
        "row_id": 0,
        "verdict": "revise",
        "revised_description": "8\" CMU partition wall, painted both sides",
    }])
    with patch.object(client, "_call", return_value=payload):
        n = client.review_low_confidence_rows(rows, threshold=0.75)
    assert n == 1
    assert rows[0].description == '8" CMU partition wall, painted both sides'
    assert rows[0].confidence == 0.9
    assert rows[0].needs_review is False
    assert rows[0].extraction_method == "reviewed"


# ── 4. Reject verdict ─────────────────────────────────────────────────────

def test_review_low_confidence_rows_skips_reject_verdict():
    client = _client()
    rows = [_make_row(description="garbage row", confidence=0.3)]
    payload = json.dumps([{"row_id": 0, "verdict": "reject"}])
    with patch.object(client, "_call", return_value=payload):
        n = client.review_low_confidence_rows(rows, threshold=0.75)
    assert n == 0  # reject not counted
    # Row left as-is.
    assert rows[0].description == "garbage row"
    assert rows[0].confidence == 0.3
    assert rows[0].needs_review is True
    assert rows[0].extraction_method == "vector"


# ── 5. API error ──────────────────────────────────────────────────────────

def test_review_low_confidence_rows_handles_api_error_gracefully():
    client = _client()
    rows = [_make_row(confidence=0.5), _make_row(confidence=0.6)]
    with patch.object(client, "_call", side_effect=RuntimeError("boom")):
        n = client.review_low_confidence_rows(rows, threshold=0.75)
    assert n == 0
    # Rows unchanged.
    assert rows[0].confidence == 0.5
    assert rows[1].confidence == 0.6


# ── 6. Invalid JSON ───────────────────────────────────────────────────────

def test_review_low_confidence_rows_handles_invalid_json_gracefully():
    client = _client()
    rows = [_make_row(confidence=0.5)]
    with patch.object(client, "_call", return_value="not json {{{"):
        n = client.review_low_confidence_rows(rows, threshold=0.75)
    assert n == 0
    assert rows[0].confidence == 0.5
    assert rows[0].needs_review is True


# ── 7. Chunking at 20 rows ────────────────────────────────────────────────

def test_review_low_confidence_rows_chunks_at_20_rows():
    client = _client()
    rows = [_make_row(confidence=0.5) for _ in range(25)]

    # Each call returns confirm verdicts for the row_ids it was sent.
    def fake_call(model, system, messages, max_tokens=None):
        sent = json.loads(messages[0]["content"])
        return json.dumps([
            {"row_id": item["row_id"], "verdict": "confirm"} for item in sent
        ])

    with patch.object(client, "_call", side_effect=fake_call) as mock_call:
        n = client.review_low_confidence_rows(rows, threshold=0.75)
    assert mock_call.call_count == 2
    # First chunk = 20 rows, second chunk = 5 rows.
    first_payload = json.loads(mock_call.call_args_list[0].args[2][0]["content"])
    second_payload = json.loads(mock_call.call_args_list[1].args[2][0]["content"])
    assert len(first_payload) == 20
    assert len(second_payload) == 5
    assert n == 25
    assert all(r.confidence == 0.9 for r in rows)


# ── 8. Header rows are excluded from review ───────────────────────────────

def test_review_low_confidence_rows_skips_header_rows():
    client = _client()
    rows = [
        _make_row(confidence=0.5, is_header=True),  # header — must be skipped
        _make_row(confidence=0.5),
    ]
    with patch.object(client, "_call",
                       return_value=json.dumps([{"row_id": 1, "verdict": "confirm"}])) as mock_call:
        n = client.review_low_confidence_rows(rows, threshold=0.75)
    assert n == 1
    # Only the non-header row was sent.
    sent = json.loads(mock_call.call_args.args[2][0]["content"])
    assert len(sent) == 1
    assert sent[0]["row_id"] == 1
    # Header row untouched.
    assert rows[0].confidence == 0.5
    assert rows[0].needs_review is True
