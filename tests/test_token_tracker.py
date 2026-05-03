"""Tests for the NVIDIA-aware token tracker buckets and recording method."""
from __future__ import annotations

from types import SimpleNamespace

from core.token_tracker import TokenTracker, _PRICING


NVIDIA_MODEL = "nvidia/nemotron-mini-4b-instruct"


def test_record_nvidia_with_dict_input():
    tracker = TokenTracker()
    tracker.record_nvidia(
        {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168},
        NVIDIA_MODEL,
    )
    bucket = tracker.usage.by_model[NVIDIA_MODEL]
    assert bucket.input_tokens == 123
    assert bucket.output_tokens == 45
    assert bucket.api_calls == 1
    assert bucket.cache_read_tokens == 0
    assert bucket.cache_write_tokens == 0


def test_record_nvidia_with_simplenamespace_input():
    tracker = TokenTracker()
    usage = SimpleNamespace(
        input_tokens=200,
        output_tokens=80,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    tracker.record_nvidia(usage, NVIDIA_MODEL)
    bucket = tracker.usage.by_model[NVIDIA_MODEL]
    assert bucket.input_tokens == 200
    assert bucket.output_tokens == 80


def test_nvidia_models_have_zero_cost():
    tracker = TokenTracker()
    for model in (
        "nvidia/nemotron-mini-4b-instruct",
        "meta/llama-4-maverick-17b-128e-instruct",
        "mistralai/mistral-nemotron",
        "nvidia/nv-embed-v1",
        "nv-rerank-qa-mistral-4b:1",
    ):
        assert model in _PRICING
        tracker.record_nvidia(
            {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
            model,
        )
    assert tracker.usage.estimated_cost_usd == 0.0


def test_record_nvidia_triggers_listeners():
    tracker = TokenTracker()
    seen: list[int] = []
    tracker.on_update(lambda usage: seen.append(usage.api_calls))
    tracker.record_nvidia(
        {"prompt_tokens": 10, "completion_tokens": 5},
        NVIDIA_MODEL,
    )
    assert seen == [1]
