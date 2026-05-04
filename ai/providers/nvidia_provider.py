"""NVIDIA NIM provider — OpenAI-compatible chat + embeddings + separate rerank URL.

Two endpoints to be aware of:

* ``chat_base_url`` — covers chat completions, vision (multimodal content
  array), and embeddings. Default ``https://integrate.api.nvidia.com/v1``.
* ``rerank_base_url`` — a *different* host. The reranker is served from
  ``https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking`` and uses a
  dedicated payload shape (``query.text`` + ``passages[].text``).

Vision is only enabled for ``meta/llama-4-maverick-17b-128e-instruct`` —
attempting any other model raises :class:`ProviderCapabilityError`.

Caching and batches are not supported. ``cache_system`` is silently ignored
on chat/vision so callers don't have to branch.
"""
from __future__ import annotations

import base64
import os
from types import SimpleNamespace
from typing import Any

import httpx

from ai.providers.base import Provider, ProviderCapabilityError
from core.token_tracker import TokenTracker


_VISION_MODEL = "meta/llama-4-maverick-17b-128e-instruct"


class NvidiaProvider:
    """Provider implementation backed by NVIDIA NIM's OpenAI-compatible API."""

    name: str = "nvidia"
    supports_caching: bool = False
    supports_batches: bool = False
    # Vision is conditionally supported (only the maverick model). Flag
    # advertises capability presence; ``vision()`` enforces the model name.
    supports_vision: bool = True
    supports_embeddings: bool = True
    supports_reranking: bool = True

    def __init__(
        self,
        config: dict,
        tracker: TokenTracker,
        *,
        client: httpx.Client | None = None,
    ):
        provider_cfg = (config.get("providers") or {}).get("nvidia") or {}
        api_key_env = provider_cfg.get("api_key_env", "NVIDIA_API_KEY")
        self._api_key = os.environ.get(api_key_env, "")
        self._chat_base_url = provider_cfg.get(
            "chat_base_url", "https://integrate.api.nvidia.com/v1"
        ).rstrip("/")
        self._rerank_base_url = provider_cfg.get(
            "rerank_base_url",
            "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking",
        )
        self._timeout_s = float(provider_cfg.get("timeout_s", 60))
        self._tracker = tracker
        self._client = client if client is not None else httpx.Client(
            timeout=self._timeout_s
        )

    # ── Chat ──────────────────────────────────────────────────────────────

    def chat(
        self,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        *,
        cache_system: bool = False,  # ignored — NIM has no prompt cache
        temperature: float | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        data = self._post_json(f"{self._chat_base_url}/chat/completions", payload)
        self._record_usage(data, model)
        return data["choices"][0]["message"]["content"]

    # ── Vision ────────────────────────────────────────────────────────────

    def vision(
        self,
        model: str,
        system: str,
        image_bytes: bytes,
        prompt: str,
        max_tokens: int,
        *,
        cache_system: bool = False,  # ignored
    ) -> str:
        if model != _VISION_MODEL:
            raise ProviderCapabilityError(
                f"NvidiaProvider.vision requires model={_VISION_MODEL!r}; "
                f"got {model!r}."
            )
        b64 = base64.standard_b64encode(image_bytes).decode()
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                },
            ],
            "max_tokens": max_tokens,
        }
        data = self._post_json(f"{self._chat_base_url}/chat/completions", payload)
        self._record_usage(data, model)
        return data["choices"][0]["message"]["content"]

    # ── Embeddings ────────────────────────────────────────────────────────

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": model,
            "input": texts,
            "encoding_format": "float",
            "extra_body": {"input_type": "query", "truncate": "NONE"},
        }
        data = self._post_json(f"{self._chat_base_url}/embeddings", payload)
        return [item["embedding"] for item in data["data"]]

    # ── Rerank ────────────────────────────────────────────────────────────

    def rerank(
        self,
        model: str,
        query: str,
        passages: list[str],
    ) -> list[tuple[int, float]]:
        payload = {
            "model": model,
            "query": {"text": query},
            "passages": [{"text": p} for p in passages],
        }
        data = self._post_json(self._rerank_base_url, payload)
        rankings = data.get("rankings", [])
        # NIM already returns rankings sorted desc by logit.
        return [(int(r["index"]), float(r["logit"])) for r in rankings]

    # ── Internals ─────────────────────────────────────────────────────────

    def _post_json(self, url: str, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        resp = self._client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def _record_usage(self, data: dict, model: str) -> None:
        """Bridge NIM's OpenAI-style usage block into the TokenTracker.

        Prefer ``tracker.record_nvidia`` if commit 6 has landed; otherwise
        synthesize a SimpleNamespace shaped like ``anthropic.types.Usage``
        so :meth:`TokenTracker.record` can ingest it without changes.
        """
        usage = data.get("usage") or {}
        record_nvidia = getattr(self._tracker, "record_nvidia", None)
        if callable(record_nvidia):
            record_nvidia(usage, model)
            return
        # Fallback path — wire prompt/completion into Anthropic-shaped Usage.
        synthetic = SimpleNamespace(
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        self._tracker.record(synthetic, model)
