"""Live token usage tracking for all Claude API calls, broken down per model."""
from dataclasses import dataclass, field
from typing import Callable

from ai.prompt_cache import PromptCacheTracker


# Anthropic pricing per 1M tokens (input / output / cache-read / cache-write).
# Cache-write is 25% surcharge on input; cache-read is 10% of input.
_PRICING = {
    "claude-haiku-4-5-20251001":   (1.00,  5.00,  0.10,  1.25),
    "claude-haiku-4-5":            (1.00,  5.00,  0.10,  1.25),
    "claude-sonnet-4-6":           (3.00, 15.00,  0.30,  3.75),
    "claude-sonnet-4-6-20250514":  (3.00, 15.00,  0.30,  3.75),
    "claude-opus-4-5":             (15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-7":             (15.00, 75.00, 1.50, 18.75),
    # Phase-7 batch tier — 50% discount across the board.
    "claude-haiku-4-5-20251001::batch":  (0.50,  2.50,  0.05,  0.625),
    "claude-haiku-4-5::batch":           (0.50,  2.50,  0.05,  0.625),
    "claude-sonnet-4-6::batch":          (1.50,  7.50,  0.15,  1.875),
    "claude-sonnet-4-6-20250514::batch": (1.50,  7.50,  0.15,  1.875),
    "claude-opus-4-5::batch":            (7.50, 37.50, 0.75,  9.375),
    "claude-opus-4-7::batch":            (7.50, 37.50, 0.75,  9.375),
}
# Fallback for unknown models — assume Sonnet pricing.
_DEFAULT_PRICE = (3.00, 15.00, 0.30, 3.75)


def _price(model: str) -> tuple[float, float, float, float]:
    return _PRICING.get(model, _DEFAULT_PRICE)


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    api_calls: int = 0

    def cost_usd(self, model: str) -> float:
        in_p, out_p, cr_p, cw_p = _price(model)
        return (
            self.input_tokens / 1_000_000 * in_p
            + self.output_tokens / 1_000_000 * out_p
            + self.cache_read_tokens / 1_000_000 * cr_p
            + self.cache_write_tokens / 1_000_000 * cw_p
        )


@dataclass
class TokenUsage:
    by_model: dict[str, ModelUsage] = field(default_factory=dict)

    @property
    def input_tokens(self) -> int:
        return sum(m.input_tokens for m in self.by_model.values())

    @property
    def output_tokens(self) -> int:
        return sum(m.output_tokens for m in self.by_model.values())

    @property
    def cache_read_tokens(self) -> int:
        return sum(m.cache_read_tokens for m in self.by_model.values())

    @property
    def cache_write_tokens(self) -> int:
        return sum(m.cache_write_tokens for m in self.by_model.values())

    @property
    def api_calls(self) -> int:
        return sum(m.api_calls for m in self.by_model.values())

    @property
    def estimated_cost_usd(self) -> float:
        return sum(usage.cost_usd(model) for model, usage in self.by_model.items())

    @property
    def cache_hit_rate(self) -> float:
        cacheable = self.cache_read_tokens + self.cache_write_tokens
        return (self.cache_read_tokens / cacheable) if cacheable else 0.0

    def add(self, usage, model: str = "claude-sonnet-4-6") -> None:
        """Accept an Anthropic Usage object (or dict) plus the model used."""
        bucket = self.by_model.setdefault(model, ModelUsage())
        if hasattr(usage, "input_tokens"):
            bucket.input_tokens += usage.input_tokens or 0
            bucket.output_tokens += usage.output_tokens or 0
            bucket.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
            bucket.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        else:
            bucket.input_tokens += usage.get("input_tokens", 0)
            bucket.output_tokens += usage.get("output_tokens", 0)
            bucket.cache_read_tokens += usage.get("cache_read_input_tokens", 0)
            bucket.cache_write_tokens += usage.get("cache_creation_input_tokens", 0)
        bucket.api_calls += 1

    def summary(self) -> str:
        per_model = " | ".join(
            f"{m}: {u.api_calls} calls, ${u.cost_usd(m):.4f}"
            for m, u in self.by_model.items()
        )
        return (
            f"Total: {self.api_calls} calls | "
            f"In: {self.input_tokens:,} | Out: {self.output_tokens:,} | "
            f"Cache R/W: {self.cache_read_tokens:,}/{self.cache_write_tokens:,} | "
            f"Hit rate: {self.cache_hit_rate * 100:.1f}% | "
            f"Cost: ${self.estimated_cost_usd:.4f}"
            + (f" || {per_model}" if per_model else "")
        )


class TokenTracker:
    def __init__(self):
        self._usage = TokenUsage()
        self._cache = PromptCacheTracker()
        self._listeners: list[Callable[[TokenUsage], None]] = []

    def record(self, usage, model: str = "claude-sonnet-4-6"):
        self._usage.add(usage, model)
        self._cache.record(model, usage)
        for fn in self._listeners:
            fn(self._usage)

    def record_batch(self, usage, model: str = "claude-sonnet-4-6"):
        """Variant for the Batches API — Anthropic's 50% discount applies.

        We record the *raw* token counts (so cache-hit and call totals
        stay accurate) but slot the cost into a synthetic discounted
        bucket so :meth:`TokenUsage.estimated_cost_usd` reflects the
        50% saving without us inventing fake token numbers.
        """
        bucket_name = f"{model}::batch"
        self._usage.add(usage, bucket_name)
        # Fold the actual model into the cache stats too so cache-hit %
        # remains meaningful per real model.
        self._cache.record(model, usage)
        for fn in self._listeners:
            fn(self._usage)

    def on_update(self, fn: Callable[[TokenUsage], None]):
        self._listeners.append(fn)

    @property
    def usage(self) -> TokenUsage:
        return self._usage

    @property
    def cache(self) -> PromptCacheTracker:
        return self._cache

    def reset(self):
        self._usage = TokenUsage()
        self._cache = PromptCacheTracker()
