"""Track prompt-cache hit rate per model.

Wraps Anthropic Usage objects and exposes cache-write vs cache-read ratios so
the UI can show how much money the prompt-cache is saving.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CacheStats:
    cache_writes: int = 0           # ephemeral cache creation tokens
    cache_reads: int = 0            # ephemeral cache read tokens
    uncached_input: int = 0         # input tokens that never touched cache

    @property
    def hit_rate(self) -> float:
        cacheable = self.cache_reads + self.cache_writes
        if cacheable == 0:
            return 0.0
        return self.cache_reads / cacheable

    def record(self, usage) -> None:
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        inp = getattr(usage, "input_tokens", 0) or 0
        self.cache_writes += cw
        self.cache_reads += cr
        # Anthropic's "input_tokens" excludes cached portions, so add directly.
        self.uncached_input += inp


@dataclass
class PromptCacheTracker:
    """Per-model cache statistics. Update by passing the model id + usage."""
    by_model: dict[str, CacheStats] = field(default_factory=dict)

    def record(self, model: str, usage) -> None:
        if model not in self.by_model:
            self.by_model[model] = CacheStats()
        self.by_model[model].record(usage)

    @property
    def overall_hit_rate(self) -> float:
        total_reads = sum(s.cache_reads for s in self.by_model.values())
        total_writes = sum(s.cache_writes for s in self.by_model.values())
        cacheable = total_reads + total_writes
        return (total_reads / cacheable) if cacheable else 0.0
