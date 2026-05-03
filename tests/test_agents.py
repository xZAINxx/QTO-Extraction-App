"""Wave-2 agent tests — function-based agents with mocked providers.

Pattern: each agent gets a ``FakeProvider`` that captures the args passed
to ``chat()``/``vision()`` and returns a canned response. We never hit a
real network. The tests verify routing (primary vs. fallback), error
handling (graceful degradation), and prompt wiring (system prompt
matches the expected source).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ai.agents import AgentContext
from ai.agents.csi_classifier import classify as classify_csi
from ai.agents.description_normalizer import normalize as normalize_description
from ai.agents.page_classifier import classify_page
from ai.agents.text_extractor import extract_from_text
from ai.agents.vision_extractor import extract_from_image
from ai.description_composer import _SYSTEM as _COMPOSER_SYSTEM
from ai.providers.base import ProviderCapabilityError


# ── Fakes ────────────────────────────────────────────────────────────────


class FakeProvider:
    """Captures call args + returns scripted responses.

    Set ``chat_response`` / ``vision_response`` to a string, an Exception
    instance (will be raised), or a callable ``(*args, **kwargs) -> str``.
    """

    def __init__(
        self,
        name: str = "fake",
        *,
        chat_response: Any = "",
        vision_response: Any = "",
        supports_vision: bool = True,
        supports_caching: bool = False,
    ):
        self.name = name
        self.supports_caching = supports_caching
        self.supports_batches = False
        self.supports_vision = supports_vision
        self.supports_embeddings = False
        self.supports_reranking = False
        self.chat_response = chat_response
        self.vision_response = vision_response
        self.chat_calls: list[dict] = []
        self.vision_calls: list[dict] = []

    def chat(self, model, system, messages, max_tokens, *, cache_system=False, temperature=None):
        self.chat_calls.append({
            "model": model, "system": system, "messages": messages,
            "max_tokens": max_tokens, "cache_system": cache_system,
            "temperature": temperature,
        })
        return _resolve(self.chat_response)

    def vision(self, model, system, image_bytes, prompt, max_tokens, *, cache_system=False):
        self.vision_calls.append({
            "model": model, "system": system, "image_bytes": image_bytes,
            "prompt": prompt, "max_tokens": max_tokens, "cache_system": cache_system,
        })
        return _resolve(self.vision_response)

    def embed(self, model, texts):
        raise ProviderCapabilityError("fake provider has no embed")

    def rerank(self, model, query, passages):
        raise ProviderCapabilityError("fake provider has no rerank")


def _resolve(value: Any) -> str:
    if isinstance(value, BaseException):
        raise value
    if callable(value):
        return value()
    return value


def _ctx(provider: FakeProvider | None = None, *, agent_config: dict | None = None,
         providers: dict | None = None) -> AgentContext:
    tracker = SimpleNamespace(record=lambda *a, **k: None)
    provs: dict = providers if providers is not None else {}
    if provider is not None:
        provs.setdefault(provider.name, provider)
    return AgentContext(
        providers=provs,
        tracker=tracker,  # type: ignore[arg-type]
        agent_config=agent_config or {},
    )


# ── page_classifier ──────────────────────────────────────────────────────


def test_page_classifier_uses_heuristic_fast_path():
    provider = FakeProvider(name="nvidia", chat_response="SCHEDULE")  # would be wrong
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia", "model": "x", "fast_path_heuristics": True,
    })
    # "DEMOLITION PLAN" hits a non-default heuristic match (PLAN_DEMO),
    # which short-circuits the LLM call. Note: "FLOOR PLAN" would *not*
    # short-circuit because the heuristic returns PLAN_CONSTRUCTION for
    # both real matches and the catch-all default — it's indistinguishable,
    # so the spec requires escalating in that case.
    result = classify_page("DEMOLITION PLAN — 2ND FLOOR", ctx)
    assert result == "PLAN_DEMO"
    assert provider.chat_calls == []  # provider never invoked


def test_page_classifier_calls_provider_when_heuristic_returns_default():
    # Text that the heuristic would *not* match against any specific type
    # (no PLAN/SCHEDULE/ELEVATION/DETAIL/LEGEND keywords). pdf_splitter
    # returns the default PLAN_CONSTRUCTION, so the agent must escalate.
    provider = FakeProvider(name="nvidia", chat_response="LEGEND_ONLY")
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia", "model": "nemotron-mini-4b",
        "max_tokens": 24, "temperature": 0.0, "fast_path_heuristics": True,
    })
    text = "Random uncategorizable header text without trigger keywords."
    result = classify_page(text, ctx)
    assert result == "LEGEND_ONLY"
    assert len(provider.chat_calls) == 1
    assert provider.chat_calls[0]["model"] == "nemotron-mini-4b"


def test_page_classifier_handles_provider_error_gracefully():
    provider = FakeProvider(name="nvidia", chat_response=RuntimeError("boom"))
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia", "model": "x", "fast_path_heuristics": True,
    })
    # Force escalation with text that doesn't trip the heuristic.
    result = classify_page("Random uncategorizable text.", ctx)
    assert result == "PLAN_CONSTRUCTION"


# ── vision_extractor ─────────────────────────────────────────────────────


def test_vision_extractor_uses_primary_provider():
    primary = FakeProvider(name="nvidia", vision_response='{"id":"K1"}')
    ctx = _ctx(primary, agent_config={
        "provider": "nvidia", "model": "meta/llama-4-maverick",
        "max_tokens": 4000,
    })
    out = extract_from_image(b"PNGDATA", "extract legend", ctx)
    assert out == '{"id":"K1"}'
    assert len(primary.vision_calls) == 1
    call = primary.vision_calls[0]
    assert call["model"] == "meta/llama-4-maverick"
    assert call["image_bytes"] == b"PNGDATA"
    assert call["prompt"] == "extract legend"
    assert call["max_tokens"] == 4000


def test_vision_extractor_falls_back_on_capability_error():
    primary = FakeProvider(
        name="nvidia",
        vision_response=ProviderCapabilityError("wrong model"),
    )
    fallback = FakeProvider(name="anthropic", vision_response="from sonnet")
    ctx = _ctx(agent_config={
        "provider": "nvidia", "model": "wrong-model",
        "fallback_provider": "anthropic", "fallback_model": "claude-sonnet-4-6",
        "max_tokens": 4000,
    }, providers={"nvidia": primary, "anthropic": fallback})
    out = extract_from_image(b"PNG", "p", ctx)
    assert out == "from sonnet"
    assert len(primary.vision_calls) == 1
    assert len(fallback.vision_calls) == 1
    assert fallback.vision_calls[0]["model"] == "claude-sonnet-4-6"


def test_vision_extractor_returns_empty_on_other_exception():
    primary = FakeProvider(name="nvidia", vision_response=RuntimeError("network"))
    ctx = _ctx(primary, agent_config={
        "provider": "nvidia", "model": "x", "max_tokens": 4000,
    })
    out = extract_from_image(b"PNG", "p", ctx)
    assert out == ""


# ── text_extractor ───────────────────────────────────────────────────────


def test_text_extractor_parses_json_array():
    provider = FakeProvider(
        name="nvidia",
        chat_response='[{"id":"K1","description":"X","qty":1,"units":"EA"}]',
    )
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia", "model": "mistral-nemotron",
        "max_tokens": 2000, "temperature": 0.0,
    })
    rows = extract_from_text("raw page text", "extract items", ctx)
    assert rows == [{"id": "K1", "description": "X", "qty": 1, "units": "EA"}]
    # User content stitches prompt + raw text together.
    user_msg = provider.chat_calls[0]["messages"][0]["content"]
    assert "extract items" in user_msg
    assert "raw page text" in user_msg


def test_text_extractor_returns_empty_on_invalid_json():
    provider = FakeProvider(name="nvidia", chat_response="not json at all")
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia", "model": "x", "max_tokens": 2000,
    })
    assert extract_from_text("text", "prompt", ctx) == []


# ── csi_classifier ───────────────────────────────────────────────────────


def test_csi_classifier_parses_json_response():
    provider = FakeProvider(
        name="nvidia",
        chat_response='{"division":"DIVISION 09","confidence":0.92}',
    )
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia", "model": "nemotron-mini-4b", "max_tokens": 64,
    })
    division, conf = classify_csi(
        "paint walls eggshell", {"DIVISION 09": ["paint"]}, ctx,
    )
    assert division == "DIVISION 09"
    assert conf == pytest.approx(0.92)


def test_csi_classifier_falls_back_to_keywords_on_error():
    provider = FakeProvider(name="nvidia", chat_response=RuntimeError("nope"))
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia", "model": "x", "max_tokens": 64,
    })
    division, conf = classify_csi(
        "paint walls eggshell",
        {"DIVISION 09": ["paint"], "DIVISION 03": ["concrete"]},
        ctx,
    )
    assert division == "DIVISION 09"
    assert conf == 0.5


# ── description_normalizer ───────────────────────────────────────────────


def test_description_normalizer_calls_provider_with_composer_system():
    provider = FakeProvider(
        name="nvidia",
        chat_response="PROVIDE & INSTALL MAPLE FLOORING @ AUDITORIUM",
    )
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia", "model": "nemotron-mini-4b", "max_tokens": 512,
    })
    out = normalize_description(
        raw="provide and install maple flooring at auditorium",
        sheet="A-102",
        keynote_ref="1/A901",
        ctx=ctx,
    )
    assert out == "PROVIDE & INSTALL MAPLE FLOORING @ AUDITORIUM"
    # Critical: the system prompt must be the verbatim composer _SYSTEM,
    # otherwise we'd lose the 13 few-shot examples that carry the format.
    assert provider.chat_calls[0]["system"] == _COMPOSER_SYSTEM
    user_msg = provider.chat_calls[0]["messages"][0]["content"]
    assert "Sheet: A-102" in user_msg
    assert "Keynote: 1/A901" in user_msg
    assert "Raw: provide and install" in user_msg


def test_description_normalizer_returns_uppercase_raw_on_error():
    provider = FakeProvider(name="nvidia", chat_response=RuntimeError("api down"))
    ctx = _ctx(provider, agent_config={
        "provider": "nvidia", "model": "x", "max_tokens": 512,
    })
    out = normalize_description("paint exterior wall", "A-201", "", ctx)
    assert out == "PAINT EXTERIOR WALL"
