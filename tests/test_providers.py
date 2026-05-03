"""Wave-1 provider tests — exercise both Anthropic and NVIDIA providers.

Style mirrors ``tests/test_batch_runner.py``:

* Mock the SDK clients (``anthropic.Anthropic`` and ``httpx.Client``).
* Use ``SimpleNamespace`` for SDK-shaped response objects.
* Assert payload shape (especially the *cache_control* wrapper, the NIM
  multimodal content array, and the **separate** rerank URL) so silent
  drift away from the documented contract trips a test.
"""
from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ai.providers import (
    AnthropicProvider,
    NvidiaProvider,
    Provider,
    ProviderCapabilityError,
)
from core.token_tracker import TokenTracker


# ── Helpers ───────────────────────────────────────────────────────────────


def _anthropic_usage(in_tok: int = 50, out_tok: int = 25):
    return SimpleNamespace(
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def _anthropic_resp(text: str = "ok"):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=_anthropic_usage(),
    )


def _make_anthropic_provider(monkeypatch) -> tuple[AnthropicProvider, MagicMock, TokenTracker]:
    fake = MagicMock()
    fake.messages.create.return_value = _anthropic_resp("hello")
    monkeypatch.setattr(
        "ai.providers.anthropic_provider.anthropic.Anthropic",
        lambda **_: fake,
    )
    tracker = TokenTracker()
    provider = AnthropicProvider({"anthropic_api_key": "test"}, tracker)
    return provider, fake, tracker


def _httpx_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _make_nvidia_provider(
    *,
    chat_response: dict | None = None,
    rerank_response: dict | None = None,
    embed_response: dict | None = None,
) -> tuple[NvidiaProvider, MagicMock, TokenTracker]:
    client = MagicMock()
    routes = {
        "chat": chat_response or {
            "choices": [{"message": {"content": "nim-reply"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        },
        "rerank": rerank_response or {
            "rankings": [
                {"index": 2, "logit": 0.9},
                {"index": 0, "logit": 0.5},
            ]
        },
        "embed": embed_response or {
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 0},
        },
    }

    def _route(url, json=None, headers=None):  # noqa: A002 - matches httpx kwarg
        if "/embeddings" in url:
            return _httpx_response(routes["embed"])
        if "rerank" in url:
            return _httpx_response(routes["rerank"])
        return _httpx_response(routes["chat"])

    client.post.side_effect = _route
    tracker = TokenTracker()
    cfg = {"providers": {"nvidia": {"api_key_env": "NVIDIA_API_KEY"}}}
    provider = NvidiaProvider(cfg, tracker, client=client)
    return provider, client, tracker


# ── Anthropic provider ────────────────────────────────────────────────────


def test_anthropic_provider_chat_uses_cache_control_when_cache_system_true(monkeypatch):
    provider, fake, _ = _make_anthropic_provider(monkeypatch)
    out = provider.chat(
        model="claude-sonnet-4-6",
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
        cache_system=True,
    )
    assert out == "hello"
    kwargs = fake.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["max_tokens"] == 64
    assert kwargs["system"] == [
        {
            "type": "text",
            "text": "SYS",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert "temperature" not in kwargs


def test_anthropic_provider_chat_no_cache_when_cache_system_false(monkeypatch):
    provider, fake, _ = _make_anthropic_provider(monkeypatch)
    provider.chat(
        model="claude-haiku-4-5",
        system="raw-system",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=24,
        temperature=0.2,
    )
    kwargs = fake.messages.create.call_args.kwargs
    assert kwargs["system"] == "raw-system"
    assert kwargs["temperature"] == 0.2


def test_anthropic_provider_vision_encodes_base64_and_records_usage(monkeypatch):
    provider, fake, tracker = _make_anthropic_provider(monkeypatch)
    fake.messages.create.return_value = _anthropic_resp("vision-text")
    image_bytes = b"\x89PNG\r\n\x1a\n-fake-image-bytes"
    expected_b64 = base64.standard_b64encode(image_bytes).decode()

    out = provider.vision(
        model="claude-sonnet-4-6",
        system="SYS",
        image_bytes=image_bytes,
        prompt="what is in this image?",
        max_tokens=512,
        cache_system=True,
    )
    assert out == "vision-text"
    kwargs = fake.messages.create.call_args.kwargs
    user_content = kwargs["messages"][0]["content"]
    assert user_content[0]["type"] == "image"
    assert user_content[0]["source"]["data"] == expected_b64
    assert user_content[0]["source"]["media_type"] == "image/png"
    assert user_content[1]["text"] == "what is in this image?"
    # Usage was forwarded to the tracker under the right model bucket.
    assert "claude-sonnet-4-6" in tracker.usage.by_model
    assert tracker.usage.by_model["claude-sonnet-4-6"].api_calls == 1


def test_anthropic_provider_embed_raises_capability_error(monkeypatch):
    provider, _, _ = _make_anthropic_provider(monkeypatch)
    with pytest.raises(ProviderCapabilityError):
        provider.embed("any-model", ["a", "b"])


def test_anthropic_provider_rerank_raises_capability_error(monkeypatch):
    provider, _, _ = _make_anthropic_provider(monkeypatch)
    with pytest.raises(ProviderCapabilityError):
        provider.rerank("any-model", "q", ["p1", "p2"])


# ── NVIDIA provider ───────────────────────────────────────────────────────


def test_nvidia_provider_chat_posts_openai_compatible_payload(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test-key")
    provider, client, _ = _make_nvidia_provider()
    out = provider.chat(
        model="nvidia/nemotron-mini-4b-instruct",
        system="SYS",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=128,
    )
    assert out == "nim-reply"
    args, kwargs = client.post.call_args
    url = args[0] if args else kwargs.get("url")
    assert url == "https://integrate.api.nvidia.com/v1/chat/completions"
    payload = kwargs["json"]
    assert payload["model"] == "nvidia/nemotron-mini-4b-instruct"
    assert payload["max_tokens"] == 128
    # System must be the FIRST message in the OpenAI-compatible payload.
    assert payload["messages"][0] == {"role": "system", "content": "SYS"}
    assert payload["messages"][1] == {"role": "user", "content": "hello"}
    assert "temperature" not in payload
    assert kwargs["headers"]["Authorization"] == "Bearer nv-test-key"


def test_nvidia_provider_chat_passes_temperature(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test-key")
    provider, client, _ = _make_nvidia_provider()
    provider.chat(
        model="nvidia/nemotron-mini-4b-instruct",
        system="SYS",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=64,
        temperature=0.0,
    )
    payload = client.post.call_args.kwargs["json"]
    assert payload["temperature"] == 0.0


def test_nvidia_provider_vision_requires_maverick_model(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test-key")
    provider, _, _ = _make_nvidia_provider()
    with pytest.raises(ProviderCapabilityError):
        provider.vision(
            model="nvidia/nemotron-mini-4b-instruct",
            system="SYS",
            image_bytes=b"png-bytes",
            prompt="describe",
            max_tokens=512,
        )


def test_nvidia_provider_vision_uses_multimodal_image_url_shape(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test-key")
    provider, client, _ = _make_nvidia_provider()
    image_bytes = b"\x89PNG\r\n\x1a\n-image"
    expected_b64 = base64.standard_b64encode(image_bytes).decode()
    out = provider.vision(
        model="meta/llama-4-maverick-17b-128e-instruct",
        system="SYS",
        image_bytes=image_bytes,
        prompt="extract legend",
        max_tokens=2000,
    )
    assert out == "nim-reply"
    payload = client.post.call_args.kwargs["json"]
    assert payload["model"] == "meta/llama-4-maverick-17b-128e-instruct"
    # System stays as a separate first message; user content is multimodal.
    assert payload["messages"][0] == {"role": "system", "content": "SYS"}
    user_msg = payload["messages"][1]
    assert user_msg["role"] == "user"
    assert user_msg["content"][0] == {"type": "text", "text": "extract legend"}
    assert user_msg["content"][1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{expected_b64}"},
    }


def test_nvidia_provider_embed_posts_to_embeddings_endpoint(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test-key")
    provider, client, _ = _make_nvidia_provider()
    embeddings = provider.embed(
        model="nvidia/nv-embed-v1",
        texts=["hello world", "second passage"],
    )
    assert embeddings == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    args, kwargs = client.post.call_args
    url = args[0] if args else kwargs.get("url")
    assert url == "https://integrate.api.nvidia.com/v1/embeddings"
    payload = kwargs["json"]
    assert payload["model"] == "nvidia/nv-embed-v1"
    assert payload["input"] == ["hello world", "second passage"]
    assert payload["encoding_format"] == "float"
    assert payload["extra_body"] == {"input_type": "query", "truncate": "NONE"}


def test_nvidia_provider_rerank_posts_to_separate_rerank_url(monkeypatch):
    """Critical: reranker hits a *different* host from chat/embeddings."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test-key")
    provider, client, _ = _make_nvidia_provider()
    out = provider.rerank(
        model="nv-rerank-qa-mistral-4b:1",
        query="patch concrete spalls",
        passages=["passage A", "passage B", "passage C"],
    )
    args, kwargs = client.post.call_args
    url = args[0] if args else kwargs.get("url")
    assert url == "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking"
    # Different host than the chat base URL — this is the whole point of
    # the second config key.
    assert "integrate.api.nvidia.com" not in url
    payload = kwargs["json"]
    assert payload["model"] == "nv-rerank-qa-mistral-4b:1"
    assert payload["query"] == {"text": "patch concrete spalls"}
    assert payload["passages"] == [
        {"text": "passage A"},
        {"text": "passage B"},
        {"text": "passage C"},
    ]
    assert out == [(2, 0.9), (0, 0.5)]


def test_nvidia_provider_records_usage_via_tracker(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test-key")
    provider, _, tracker = _make_nvidia_provider()
    provider.chat(
        model="nvidia/nemotron-mini-4b-instruct",
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=24,
    )
    # Fallback path (no record_nvidia yet) lands the call under the model
    # bucket via TokenTracker.record(...).
    assert "nvidia/nemotron-mini-4b-instruct" in tracker.usage.by_model
    bucket = tracker.usage.by_model["nvidia/nemotron-mini-4b-instruct"]
    assert bucket.api_calls == 1
    assert bucket.input_tokens == 12
    assert bucket.output_tokens == 7


def test_nvidia_provider_records_usage_via_record_nvidia_when_present(monkeypatch):
    """If commit 6 has landed and ``tracker.record_nvidia`` exists, prefer it."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test-key")
    client = MagicMock()
    client.post.return_value = _httpx_response({
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    })
    tracker = MagicMock(spec=["record", "record_nvidia"])
    tracker.record_nvidia = MagicMock()
    provider = NvidiaProvider(
        {"providers": {"nvidia": {"api_key_env": "NVIDIA_API_KEY"}}},
        tracker,
        client=client,
    )
    provider.chat(
        model="nvidia/nemotron-mini-4b-instruct",
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=24,
    )
    tracker.record_nvidia.assert_called_once_with(
        {"prompt_tokens": 5, "completion_tokens": 3},
        "nvidia/nemotron-mini-4b-instruct",
    )
    tracker.record.assert_not_called()


# ── Protocol conformance ─────────────────────────────────────────────────


def test_both_providers_satisfy_provider_protocol(monkeypatch):
    """isinstance check against the runtime-checkable Protocol."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test-key")
    fake = MagicMock()
    monkeypatch.setattr(
        "ai.providers.anthropic_provider.anthropic.Anthropic",
        lambda **_: fake,
    )
    anthropic_p = AnthropicProvider({"anthropic_api_key": "x"}, TokenTracker())
    nvidia_p, _, _ = _make_nvidia_provider()
    assert isinstance(anthropic_p, Provider)
    assert isinstance(nvidia_p, Provider)
