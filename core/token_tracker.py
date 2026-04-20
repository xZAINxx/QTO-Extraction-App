"""Live token usage tracking for all Claude API calls."""
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    api_calls: int = 0

    @property
    def estimated_cost_usd(self) -> float:
        # claude-sonnet-4-6 pricing per 1M tokens
        input_cost = (self.input_tokens / 1_000_000) * 3.00
        output_cost = (self.output_tokens / 1_000_000) * 15.00
        cache_read_cost = (self.cache_read_tokens / 1_000_000) * 0.30
        cache_write_cost = (self.cache_write_tokens / 1_000_000) * 3.75
        return input_cost + output_cost + cache_read_cost + cache_write_cost

    def add(self, usage):
        """Accept an Anthropic Usage object or dict."""
        if hasattr(usage, "input_tokens"):
            self.input_tokens += usage.input_tokens or 0
            self.output_tokens += usage.output_tokens or 0
            self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
            self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        else:
            self.input_tokens += usage.get("input_tokens", 0)
            self.output_tokens += usage.get("output_tokens", 0)
            self.cache_read_tokens += usage.get("cache_read_input_tokens", 0)
            self.cache_write_tokens += usage.get("cache_creation_input_tokens", 0)
        self.api_calls += 1

    def summary(self) -> str:
        return (
            f"API calls: {self.api_calls} | "
            f"In: {self.input_tokens:,} | Out: {self.output_tokens:,} | "
            f"Cache-R: {self.cache_read_tokens:,} | Cache-W: {self.cache_write_tokens:,} | "
            f"Cost: ${self.estimated_cost_usd:.4f}"
        )


class TokenTracker:
    def __init__(self):
        self._usage = TokenUsage()
        self._listeners: list[Callable[[TokenUsage], None]] = []

    def record(self, usage):
        self._usage.add(usage)
        for fn in self._listeners:
            fn(self._usage)

    def on_update(self, fn: Callable[[TokenUsage], None]):
        self._listeners.append(fn)

    @property
    def usage(self) -> TokenUsage:
        return self._usage

    def reset(self):
        self._usage = TokenUsage()
