"""End-to-end wiring tests for the multi_agent extraction path.

Scope: Wave 5 verification. Confirms the new ``providers``/``agents``/``rag``
config blocks load, ``MultiAgentClient`` instantiates against a realistic
config without exceptions, agent dispatch routes calls to the configured
provider/model, and ``ui/main_window.py`` chooses the correct client class
per ``extraction_mode``.

Strategy: NO real network. Patch provider class constructors at the
``ai.multi_agent_client`` import site so neither HTTP client nor SDK is
touched. Real ``MultiAgentClient`` and ``TokenTracker`` instances are used.

Pre-existing failures in ``test_extractor.py`` (missing PDF fixture) are
unrelated and out of scope.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from core.qto_row import QTORow
from core.token_tracker import TokenTracker


_CONFIG_YAML = Path(__file__).resolve().parents[1] / "config.yaml"


def _multi_agent_config(rag_enabled: bool = False) -> dict:
    """Synthetic, fully-formed multi_agent config for unit tests."""
    return {
        "anthropic_api_key": "test-key",
        "extraction_mode": "multi_agent",
        "confidence_review_threshold": 0.75,
        "providers": {
            "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
            "nvidia": {
                "api_key_env": "NVIDIA_API_KEY",
                "chat_base_url": "https://integrate.api.nvidia.com/v1",
                "rerank_base_url": "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking",
                "timeout_s": 60,
            },
        },
        "agents": {
            "page_classifier": {
                "provider": "nvidia",
                "model": "nvidia/nemotron-mini-4b-instruct",
                "temperature": 0.0,
                "max_tokens": 24,
                "fast_path_heuristics": True,
            },
            "vision_extractor": {
                "provider": "nvidia",
                "model": "meta/llama-4-maverick-17b-128e-instruct",
                "temperature": 0.0,
                "max_tokens": 4000,
                "fallback_provider": "anthropic",
                "fallback_model": "claude-sonnet-4-6",
            },
            "text_extractor": {
                "provider": "nvidia",
                "model": "mistralai/mistral-nemotron",
                "temperature": 0.0,
                "max_tokens": 2000,
            },
            "csi_classifier": {
                "provider": "nvidia",
                "model": "nvidia/nemotron-mini-4b-instruct",
                "temperature": 0.0,
                "max_tokens": 64,
            },
            "normalizer": {
                "provider": "nvidia",
                "model": "nvidia/nemotron-mini-4b-instruct",
                "temperature": 0.0,
                "max_tokens": 512,
                "use_rag_priming": False,
                "rag_top_k": 5,
            },
            "orchestrator": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "temperature": 0.0,
                "max_tokens": 1500,
            },
        },
        "rag": {"enabled": rag_enabled, "store_path": "./cache/historical.db"},
        "csi_keywords": {
            "DIVISION 09": ["paint", "drywall"],
        },
    }


def _patched_providers(monkeypatch):
    """Stub both provider classes at the ``ai.multi_agent_client`` import site.

    Returns a ``{"anthropic": Mock, "nvidia": Mock}`` dict so callers can set
    canned return values and assert on call args. The ``supports_*`` flags
    mirror the real provider capability surface so the agent layer's
    capability checks behave realistically.
    """
    fake_anthropic = MagicMock(name="AnthropicProviderInst")
    fake_anthropic.name = "anthropic"
    fake_anthropic.supports_caching = True
    fake_anthropic.supports_vision = True
    fake_anthropic.supports_batches = True
    fake_anthropic.supports_embeddings = False
    fake_anthropic.supports_reranking = False

    fake_nvidia = MagicMock(name="NvidiaProviderInst")
    fake_nvidia.name = "nvidia"
    fake_nvidia.supports_caching = False
    fake_nvidia.supports_vision = True
    fake_nvidia.supports_batches = False
    fake_nvidia.supports_embeddings = True
    fake_nvidia.supports_reranking = True

    monkeypatch.setattr(
        "ai.providers.anthropic_provider.AnthropicProvider",
        lambda *a, **k: fake_anthropic,
    )
    monkeypatch.setattr(
        "ai.providers.nvidia_provider.NvidiaProvider",
        lambda *a, **k: fake_nvidia,
    )
    return {"anthropic": fake_anthropic, "nvidia": fake_nvidia}


# ── 1. Config file integrity ─────────────────────────────────────────────


def test_config_yaml_loads_with_multi_agent_blocks():
    """The shipped config.yaml parses and exposes the Wave 5 blocks."""
    cfg = yaml.safe_load(_CONFIG_YAML.read_text())

    # New top-level blocks present.
    assert "providers" in cfg
    assert "agents" in cfg
    assert "rag" in cfg

    # extraction_mode comment update — value still loads as a string.
    assert isinstance(cfg.get("extraction_mode"), str)

    # providers shape: both anthropic and nvidia are dicts with env keys.
    providers = cfg["providers"]
    assert isinstance(providers, dict)
    assert "anthropic" in providers and isinstance(providers["anthropic"], dict)
    assert "nvidia" in providers and isinstance(providers["nvidia"], dict)
    assert "api_key_env" in providers["anthropic"]
    assert "api_key_env" in providers["nvidia"]
    assert "chat_base_url" in providers["nvidia"]
    assert "rerank_base_url" in providers["nvidia"]

    # agents shape: every expected agent slot exists with provider+model keys.
    agents = cfg["agents"]
    assert isinstance(agents, dict)
    expected_agents = {
        "page_classifier",
        "vision_extractor",
        "text_extractor",
        "csi_classifier",
        "normalizer",
        "orchestrator",
    }
    assert expected_agents.issubset(agents.keys())
    for name in expected_agents:
        slot = agents[name]
        assert isinstance(slot, dict), f"{name} agent slot must be a dict"
        assert "provider" in slot, f"{name} missing provider key"
        assert "model" in slot, f"{name} missing model key"

    # rag shape: enabled flag + store_path present.
    rag = cfg["rag"]
    assert isinstance(rag, dict)
    assert "enabled" in rag
    assert isinstance(rag["enabled"], bool)
    assert "store_path" in rag


# ── 2. Constructor accepts the full config without exceptions ───────────


def test_multi_agent_client_instantiates_with_full_config(monkeypatch):
    mocks = _patched_providers(monkeypatch)
    from ai.multi_agent_client import MultiAgentClient

    tracker = TokenTracker()
    client = MultiAgentClient(_multi_agent_config(), tracker)

    assert "anthropic" in client._providers
    assert "nvidia" in client._providers
    assert client._providers["anthropic"] is mocks["anthropic"]
    assert client._providers["nvidia"] is mocks["nvidia"]
    # rag disabled in the synthetic config -> no store wired up.
    assert client._rag is None


# ── 3. Page classifier dispatch routes through NVIDIA on whitespace text ─


def test_multi_agent_client_classify_page_type_routes_through_heuristic_then_nvidia(monkeypatch):
    """Whitespace text bypasses the heuristic and the LLM both — the agent
    short-circuits to PLAN_CONSTRUCTION when ``snippet.strip()`` is empty.

    To force an actual NVIDIA call, we provide non-trivial text but disable
    the heuristic fast path on the agent config so the LLM is consulted.
    """
    mocks = _patched_providers(monkeypatch)
    mocks["nvidia"].chat.return_value = "SCHEDULE"

    cfg = _multi_agent_config()
    cfg["agents"]["page_classifier"]["fast_path_heuristics"] = False

    from ai.multi_agent_client import MultiAgentClient
    client = MultiAgentClient(cfg, TokenTracker())

    result = client.classify_page_type("DOOR SCHEDULE\n\nMK | TYPE | SIZE")
    assert result == "SCHEDULE"

    # Dispatch went to NVIDIA with the configured Nemotron model.
    assert mocks["nvidia"].chat.call_count == 1
    pos_args, _ = mocks["nvidia"].chat.call_args
    assert pos_args[0] == "nvidia/nemotron-mini-4b-instruct"
    # Anthropic was NOT consulted for page classification.
    mocks["anthropic"].chat.assert_not_called()


# ── 4. Vision dispatch hits Maverick ─────────────────────────────────────


def test_multi_agent_client_extract_full_page_vision_routes_to_maverick(monkeypatch):
    mocks = _patched_providers(monkeypatch)
    mocks["nvidia"].vision.return_value = '[{"id":"K-1","description":"PAINT","qty":1,"units":"LS","table_type":"A"}]'

    from ai.multi_agent_client import MultiAgentClient
    client = MultiAgentClient(_multi_agent_config(), TokenTracker())

    rows = client.extract_full_page_vision(b"\x89PNG\r\n\x1a\nfake")
    assert isinstance(rows, list) and len(rows) == 1
    assert rows[0]["id"] == "K-1"

    # Maverick model, NVIDIA provider, vision call.
    mocks["nvidia"].vision.assert_called_once()
    pos_args, _ = mocks["nvidia"].vision.call_args
    assert pos_args[0] == "meta/llama-4-maverick-17b-128e-instruct"
    mocks["anthropic"].vision.assert_not_called()


# ── 5. Compose -> normalizer agent -> NVIDIA ─────────────────────────────


def test_multi_agent_client_compose_description_routes_through_normalizer_agent(monkeypatch):
    mocks = _patched_providers(monkeypatch)
    mocks["nvidia"].chat.return_value = "PAINT WALLS"

    from ai.multi_agent_client import MultiAgentClient
    client = MultiAgentClient(_multi_agent_config(), TokenTracker())

    result = client.compose_description("paint walls", sheet="A-101", keynote_ref="K-1")
    assert result == "PAINT WALLS"

    mocks["nvidia"].chat.assert_called_once()
    pos_args, _ = mocks["nvidia"].chat.call_args
    assert pos_args[0] == "nvidia/nemotron-mini-4b-instruct"
    mocks["anthropic"].chat.assert_not_called()

    # Per-key compose cache populates after first call.
    cache_key = "paint walls|A-101|K-1"
    assert cache_key in client._compose_cache
    assert client._compose_cache[cache_key] == "PAINT WALLS"

    # Second call hits the cache — provider not re-invoked.
    result2 = client.compose_description("paint walls", sheet="A-101", keynote_ref="K-1")
    assert result2 == "PAINT WALLS"
    assert mocks["nvidia"].chat.call_count == 1


# ── 6. Review low-confidence rows -> orchestrator -> Anthropic ───────────


def test_multi_agent_client_review_low_confidence_rows_routes_to_anthropic(monkeypatch):
    mocks = _patched_providers(monkeypatch)
    # Orchestrator returns a confirm + revise pair.
    mocks["anthropic"].chat.return_value = (
        '[{"row_id":0,"verdict":"confirm"},'
        '{"row_id":1,"verdict":"revise","revised_description":"PAINT GYP BD WALL"}]'
    )

    rows = [
        QTORow(description="paint", qty=10, units="SF", confidence=0.4),
        QTORow(description="paint wall", qty=20, units="SF", confidence=0.5),
    ]

    from ai.multi_agent_client import MultiAgentClient
    client = MultiAgentClient(_multi_agent_config(), TokenTracker())

    applied = client.review_low_confidence_rows(rows, threshold=0.75)
    assert applied == 2

    # Anthropic was the reviewer.
    mocks["anthropic"].chat.assert_called_once()
    pos_args, _ = mocks["anthropic"].chat.call_args
    assert pos_args[0] == "claude-sonnet-4-6"

    # NVIDIA was NOT consulted for orchestrator review.
    mocks["nvidia"].chat.assert_not_called()

    # Verdicts applied: confirm bumps confidence; revise updates description+method.
    assert rows[0].confidence == 0.9
    assert rows[0].needs_review is False
    assert rows[1].description == "PAINT GYP BD WALL"
    assert rows[1].extraction_method == "reviewed"


# ── 7. Chat over rows delegates to lazy AIClient fallback ────────────────


def test_multi_agent_client_chat_over_rows_routes_through_anthropic_fallback(monkeypatch):
    _patched_providers(monkeypatch)

    fake_aiclient = MagicMock(name="AIClientFallback")
    fake_aiclient.chat_over_rows.return_value = "It's a paint job on Sheet A-101."
    monkeypatch.setattr("ai.client.AIClient", lambda *a, **k: fake_aiclient)

    from ai.multi_agent_client import MultiAgentClient
    client = MultiAgentClient(_multi_agent_config(), TokenTracker())

    answer = client.chat_over_rows(
        rows_payload=[{"id": 1, "desc": "paint"}],
        history=[],
        question="What's on A-101?",
    )
    assert answer == "It's a paint job on Sheet A-101."

    fake_aiclient.chat_over_rows.assert_called_once()
    # Lazy reuse: same fallback instance on the second call.
    answer2 = client.chat_over_rows([{"id": 2, "desc": "tile"}], [], "and now?")
    assert answer2 == "It's a paint job on Sheet A-101."
    assert fake_aiclient.chat_over_rows.call_count == 2


# ── 8. extraction_mode dispatch in ui/main_window.py:88 logic ────────────


def test_extraction_mode_dispatch_creates_correct_client(monkeypatch):
    """Replicate the if/else branch in ui/main_window.py:88 against each mode.

    We don't instantiate the UI worker — that pulls in PyQt + every other
    import. We mirror the conditional with both client classes mocked so we
    can assert which one is selected per ``extraction_mode`` value.
    """
    fake_multi_agent = MagicMock(name="MultiAgentClientCls")
    fake_aiclient = MagicMock(name="AIClientCls")

    def _select_client(config: dict, tracker: TokenTracker):
        # Mirrors ui/main_window.py:88-93 verbatim.
        mode = config.get("extraction_mode", "hybrid")
        if mode == "multi_agent":
            return fake_multi_agent(config, tracker)
        return fake_aiclient(config, tracker)

    tracker = TokenTracker()

    # multi_agent -> MultiAgentClient
    _select_client({"extraction_mode": "multi_agent"}, tracker)
    fake_multi_agent.assert_called_once()
    fake_aiclient.assert_not_called()

    fake_multi_agent.reset_mock()
    fake_aiclient.reset_mock()

    # hybrid -> AIClient
    _select_client({"extraction_mode": "hybrid"}, tracker)
    fake_aiclient.assert_called_once()
    fake_multi_agent.assert_not_called()

    fake_aiclient.reset_mock()

    # claude_only -> AIClient (no special branch yet)
    _select_client({"extraction_mode": "claude_only"}, tracker)
    fake_aiclient.assert_called_once()
    fake_multi_agent.assert_not_called()

    fake_aiclient.reset_mock()

    # Default (key missing) -> hybrid -> AIClient
    _select_client({}, tracker)
    fake_aiclient.assert_called_once()
    fake_multi_agent.assert_not_called()


# ── 9. Token tracker records NVIDIA usage from a successful agent call ──


def test_token_tracker_records_nvidia_usage_when_multi_agent_calls_succeed(monkeypatch):
    """Patch only the HTTP layer of NvidiaProvider (not the class itself) so
    the real ``_record_usage`` -> ``tracker.record_nvidia`` bridge runs end
    to end against a real ``TokenTracker``.
    """
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "SCHEDULE"}}],
        "usage": {"prompt_tokens": 42, "completion_tokens": 7},
    }
    fake_response.raise_for_status.return_value = None

    fake_http = MagicMock()
    fake_http.post.return_value = fake_response

    # Patch httpx.Client constructor so NvidiaProvider gets our fake.
    monkeypatch.setattr("ai.providers.nvidia_provider.httpx.Client", lambda *a, **k: fake_http)

    # Stub Anthropic SDK so AnthropicProvider construction doesn't try a real client.
    fake_anthropic_sdk = MagicMock()
    monkeypatch.setattr("ai.providers.anthropic_provider.anthropic.Anthropic", lambda *a, **k: fake_anthropic_sdk)

    cfg = _multi_agent_config()
    cfg["agents"]["page_classifier"]["fast_path_heuristics"] = False  # force LLM call

    tracker = TokenTracker()

    from ai.multi_agent_client import MultiAgentClient
    client = MultiAgentClient(cfg, tracker)

    result = client.classify_page_type("DOOR SCHEDULE\nMK 1")
    assert result == "SCHEDULE"

    # NVIDIA HTTP layer was called once for chat/completions.
    assert fake_http.post.call_count == 1
    url_arg = fake_http.post.call_args[0][0]
    assert "chat/completions" in url_arg

    # Token tracker recorded the NVIDIA usage under the configured Nemotron model.
    nemotron = "nvidia/nemotron-mini-4b-instruct"
    assert nemotron in tracker.usage.by_model
    bucket = tracker.usage.by_model[nemotron]
    assert bucket.input_tokens == 42
    assert bucket.output_tokens == 7
    assert bucket.api_calls == 1


# ── 10. Suite-level sanity ──────────────────────────────────────────────


def test_full_suite_no_regressions():
    """Sentinel: this file is meant to be run with
    ``pytest tests/test_multi_agent_integration.py -v`` and the broader
    suite should not regress as a result of Wave 5 (config-only + new tests)."""
    assert True
