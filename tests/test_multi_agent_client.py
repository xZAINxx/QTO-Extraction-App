"""Tests for MultiAgentClient — the parallel-to-AIClient surface used in
``extraction_mode == "multi_agent"``.

We mock both providers at the class boundary (so neither HTTP client nor
SDK is instantiated) and patch each agent function at its module path so
we can assert the dispatch shape without touching the prompts themselves.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.qto_row import QTORow
from core.token_tracker import TokenTracker


# ── Shared fixture: zero-network MultiAgentClient ──────────────────────


def _patched_client(monkeypatch, config: dict | None = None):
    """Build a MultiAgentClient with both provider classes stubbed.

    Returns ``(client, tracker, provider_mocks)``. Patches occur before
    the import inside ``MultiAgentClient.__init__`` resolves, so neither
    real provider is touched.
    """
    cfg = config or {"anthropic_api_key": "test"}
    fake_anthropic = MagicMock(name="AnthropicProviderInst")
    fake_nvidia = MagicMock(name="NvidiaProviderInst")
    fake_anthropic.name = "anthropic"
    fake_nvidia.name = "nvidia"
    fake_anthropic.supports_caching = True
    fake_nvidia.supports_caching = False

    monkeypatch.setattr(
        "ai.providers.anthropic_provider.AnthropicProvider",
        lambda *a, **k: fake_anthropic,
    )
    monkeypatch.setattr(
        "ai.providers.nvidia_provider.NvidiaProvider",
        lambda *a, **k: fake_nvidia,
    )

    from ai.multi_agent_client import MultiAgentClient
    tracker = TokenTracker()
    client = MultiAgentClient(cfg, tracker)
    return client, tracker, {"anthropic": fake_anthropic, "nvidia": fake_nvidia}


# ── 1. Constructor wiring ────────────────────────────────────────────────


def test_multi_agent_client_constructor_initializes_providers(monkeypatch):
    client, _, mocks = _patched_client(monkeypatch)
    assert "anthropic" in client._providers
    assert "nvidia" in client._providers
    assert client._providers["anthropic"] is mocks["anthropic"]
    assert client._providers["nvidia"] is mocks["nvidia"]


def test_multi_agent_client_constructor_initializes_caches(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    assert client._classify_cache == {}
    assert client._compose_cache == {}
    assert client._page_type_cache == {}
    assert client._scope_cache == {}


# ── 2. Page classifier dispatch ──────────────────────────────────────────


def test_multi_agent_client_classify_page_type_calls_page_classifier_agent(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    with patch("ai.agents.page_classifier.classify_page", return_value="SCHEDULE") as mock_fn:
        result = client.classify_page_type("DRAWING SCHEDULE")
    assert result == "SCHEDULE"
    assert mock_fn.call_count == 1
    args, _ = mock_fn.call_args
    assert args[0] == "DRAWING SCHEDULE"
    # Second arg is an AgentContext
    assert hasattr(args[1], "providers")
    assert hasattr(args[1], "agent_config")


# ── 3. compose_description caching ───────────────────────────────────────


def test_multi_agent_client_compose_description_caches(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    with patch(
        "ai.agents.description_normalizer.normalize",
        return_value="COMPOSED",
    ) as mock_fn:
        out1 = client.compose_description("paint wall", sheet="A-101", keynote_ref="1/A101")
        out2 = client.compose_description("paint wall", sheet="A-101", keynote_ref="1/A101")
    assert out1 == "COMPOSED"
    assert out2 == "COMPOSED"
    assert mock_fn.call_count == 1, "Cache hit on second call"


def test_multi_agent_client_compose_description_separate_keys_call_twice(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    with patch(
        "ai.agents.description_normalizer.normalize",
        side_effect=["A", "B"],
    ) as mock_fn:
        client.compose_description("paint", sheet="A-101", keynote_ref="1/A101")
        client.compose_description("paint", sheet="A-102", keynote_ref="1/A102")
    assert mock_fn.call_count == 2


# ── 4. extract_full_page_vision parsing ──────────────────────────────────


def test_multi_agent_client_extract_full_page_vision_parses_json(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    with patch(
        "ai.agents.vision_extractor.extract_from_image",
        return_value='[{"id":"K1","description":"item","qty":1,"units":"EA","table_type":"A"}]',
    ):
        out = client.extract_full_page_vision(b"PNG_BYTES")
    assert isinstance(out, list)
    assert out == [{"id": "K1", "description": "item", "qty": 1, "units": "EA", "table_type": "A"}]


def test_multi_agent_client_extract_full_page_vision_returns_empty_on_invalid_json(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    with patch(
        "ai.agents.vision_extractor.extract_from_image",
        return_value="not json at all",
    ):
        out = client.extract_full_page_vision(b"PNG")
    assert out == []


def test_multi_agent_client_extract_page_claude_only_aliases_full_page_vision(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    with patch(
        "ai.agents.vision_extractor.extract_from_image",
        return_value="[]",
    ) as mock_fn:
        out = client.extract_page_claude_only(b"PNG")
    assert out == []
    assert mock_fn.call_count == 1


# ── 5. chat_over_rows delegation to lazy AIClient fallback ───────────────


def test_multi_agent_client_chat_over_rows_delegates_to_anthropic_fallback(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)

    fake_aiclient = MagicMock()
    fake_aiclient.chat_over_rows.return_value = "fallback answer"

    monkeypatch.setattr("ai.client.AIClient", lambda *a, **k: fake_aiclient)

    result = client.chat_over_rows([{"row": 1}], history=[], question="how many?", max_tokens=500)
    assert result == "fallback answer"
    fake_aiclient.chat_over_rows.assert_called_once_with([{"row": 1}], [], "how many?", 500)
    # Reuses the same fallback instance on subsequent calls.
    client.chat_over_rows([{"row": 2}], history=[], question="another?")
    assert fake_aiclient.chat_over_rows.call_count == 2


def test_multi_agent_client_describe_diff_cluster_delegates_to_fallback(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    fake_aiclient = MagicMock()
    fake_aiclient.describe_diff_cluster.return_value = "old vs new diff"
    monkeypatch.setattr("ai.client.AIClient", lambda *a, **k: fake_aiclient)
    out = client.describe_diff_cluster(b"OLD", b"NEW", sheet_id="A-101")
    assert out == "old vs new diff"
    fake_aiclient.describe_diff_cluster.assert_called_once_with(b"OLD", b"NEW", sheet_id="A-101")


# ── 6. Phase-7 stubs ─────────────────────────────────────────────────────


def test_multi_agent_client_phase7_stubs_return_safe_defaults(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    assert client.cost_saver_mode is False
    assert client.pending_compose_count == 0
    assert client.flush_pending_compose() == 0
    assert client.flush_pending_compose(on_progress=lambda *_: None) == 0


# ── 7. review_low_confidence_rows dispatch ───────────────────────────────


def test_multi_agent_client_review_low_confidence_rows_calls_orchestrator_agent(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    rows = [QTORow(description="x", confidence=0.5)]
    with patch("ai.agents.orchestrator.review_rows", return_value=1) as mock_fn:
        n = client.review_low_confidence_rows(rows, threshold=0.75)
    assert n == 1
    args, _ = mock_fn.call_args
    assert args[0] is rows
    assert args[1] == 0.75
    assert hasattr(args[2], "providers")  # AgentContext


# ── 8. Vision extractor aliases all share one delegate ───────────────────


def test_multi_agent_client_vision_aliases_call_extract_from_image(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    with patch("ai.agents.vision_extractor.extract_from_image", return_value="result") as mock_fn:
        assert client.extract_legend_from_image(b"P", "p1") == "result"
        assert client.extract_title_block_vision(b"P", "p2") == "result"
        assert client.extract_schedule_from_image(b"P", "p3") == "result"
        assert client.interpret_image_region(b"P", "p4") == "result"
    assert mock_fn.call_count == 4


# ── 9. CSI classifier dispatch ───────────────────────────────────────────


def test_multi_agent_client_classify_csi_calls_csi_classifier_agent(monkeypatch):
    client, _, _ = _patched_client(monkeypatch)
    with patch("ai.agents.csi_classifier.classify", return_value=("DIVISION 09", 0.85)) as mock_fn:
        div, conf = client.classify_csi("paint walls", {"DIVISION 09": ["paint"]})
    assert div == "DIVISION 09"
    assert conf == 0.85
    assert mock_fn.call_count == 1


# ── 10. Assembler integration: review fires on flush ─────────────────────


def test_assembler_flush_batched_compose_invokes_review_when_present():
    """Assembler.flush_batched_compose calls review_low_confidence_rows when AI exposes it."""
    from core.assembler import Assembler

    review_calls: list[tuple] = []

    class FakeAI:
        cost_saver_mode = False
        pending_compose_count = 0

        def review_low_confidence_rows(self, rows, threshold=0.75):
            review_calls.append((list(rows), threshold))
            return 2

    asm = Assembler(
        {"units_canonical": {}, "confidence_review_threshold": 0.8},
        FakeAI(),
        TokenTracker(),
    )
    rows = [QTORow(description="x", confidence=0.5, source_sheet="A-1", source_page=1)]
    upgraded = asm.flush_batched_compose(rows)
    assert upgraded == 0  # cost-saver off, no batch flush
    assert len(review_calls) == 1
    assert review_calls[0][1] == 0.8


def test_assembler_flush_batched_compose_skips_review_when_method_missing():
    """No review_low_confidence_rows attr → no error, no call."""
    from core.assembler import Assembler

    class FakeAI:
        cost_saver_mode = False
        pending_compose_count = 0

    asm = Assembler({"units_canonical": {}}, FakeAI(), TokenTracker())
    rows = [QTORow(description="x", confidence=0.5, source_sheet="A-1", source_page=1)]
    # Should not raise.
    upgraded = asm.flush_batched_compose(rows)
    assert upgraded == 0


def test_assembler_flush_batched_compose_review_runs_alongside_batch_flush():
    """Both cost-saver batch flush AND review fire when both are applicable."""
    from core.assembler import Assembler

    class FakeAI:
        def __init__(self):
            self.cost_saver_mode = True
            self.pending_compose_count = 1
            self._cache: dict[str, str] = {}
            self.review_called = False

        def compose_description(self, raw, sheet="", keynote_ref=""):
            key = f"{raw}|{sheet}|{keynote_ref}"
            return self._cache.get(key, raw.upper())

        def flush_pending_compose(self, on_progress=None):
            self._cache["paint|A-101|1/A101"] = "PRIME AND PAINT"
            return 1

        def review_low_confidence_rows(self, rows, threshold=0.75):
            self.review_called = True
            return 0

    ai = FakeAI()
    asm = Assembler({"units_canonical": {}}, ai, TokenTracker())
    row = QTORow(description="PAINT", source_sheet="A-101", source_page=10)
    asm._compose_ctx[id(row)] = ("paint", "A-101", "1/A101")
    upgraded = asm.flush_batched_compose([row])
    assert upgraded == 1
    assert row.description == "PRIME AND PAINT"
    assert ai.review_called is True
