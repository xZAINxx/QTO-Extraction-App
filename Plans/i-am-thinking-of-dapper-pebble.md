# Multi-Agent Refactor — QTO Extraction Tool

## Context

The Zeconic QTO tool currently uses Claude (Anthropic) as the sole AI for every step: page-type classification, vision extraction, text parsing, CSI classification, and description normalization. Token spend is dominated by Sonnet vision crops on legends/schedules and Sonnet `compose_description` calls (one per row). The user wants to shift the heavy-lift inference to cheaper/faster NVIDIA NIM models (Nemotron-Mini, Llama-4-Maverick, Mistral-Nemotron, NV-Embed, NV-Rerank) and reserve Claude for orchestration and review of low-confidence rows. The refactor must be **additive** — existing `hybrid` and `claude_only` modes must keep working unchanged so we can A/B-compare cost/quality.

## Mental-Model Corrections

The refactor brief named files that do not exist as separate modules:

- **`csi_classifier.py` does not exist.** CSI logic lives in [ai/client.py:185](ai/client.py:185) `classify_csi()` (marked "kept for backward compat post-Step-11" — no live callers). The new agent revives this surface as a real, used component.
- **`description_normalizer.py` does not exist.** GC-format normalization is [ai/description_composer.py](ai/description_composer.py) (the `_SYSTEM` prompt + 13 few-shots) plus [ai/client.py:207](ai/client.py:207) `compose_description()` invoked per row from [core/assembler.py:266](core/assembler.py:266). The new agent swaps *who answers `compose_description`*, no new file required.
- **`pdf_splitter.py` makes zero AI calls.** Page classification is pure text heuristics ([parser/pdf_splitter.py:39](parser/pdf_splitter.py:39)). `AIClient.classify_page_type()` ([ai/client.py:149](ai/client.py:149)) exists but has no production caller. Adding a NIM agent here is *new behavior*, not a swap. Recommendation: keep heuristics as fast-path, call the NIM agent only when the heuristic returns the default fallback (`PLAN_CONSTRUCTION`).
- **No RAG / vector store exists.** [core/cache.py](core/cache.py) is a single-table SQLite extractions store keyed by PDF fingerprint. The historical line-item store is greenfield.
- **No agent abstraction exists.** All extractors are module-level functions taking an `ai_client`. Class-based agents would be inconsistent — go function-based.

## Design Decisions

### 1. Provider Abstraction — `ai/providers/`

New package, three files. Use a `Protocol` with **capability flags**, not a symmetric ABC, to honor the real asymmetry (Anthropic has caching+batches; NVIDIA has embeddings+rerank).

```
ai/providers/
  __init__.py
  base.py              # Provider protocol + ProviderCapabilityError
  anthropic_provider.py
  nvidia_provider.py
```

Interface:

```python
class Provider(Protocol):
    name: str
    supports_caching: bool
    supports_batches: bool
    supports_vision: bool
    supports_embeddings: bool
    supports_reranking: bool

    def chat(model, system, messages, max_tokens, *, cache_system=False, temperature=None) -> str
    def vision(model, system, image_bytes, prompt, max_tokens, *, cache_system=False) -> str
    def embed(model, texts) -> list[list[float]]
    def rerank(model, query, passages) -> list[tuple[int, float]]
```

- `AnthropicProvider` reuses logic from [ai/client.py:86](ai/client.py:86) `_call` and [ai/client.py:108](ai/client.py:108) `_vision_call`. `embed`/`rerank` raise `ProviderCapabilityError`.
- `NvidiaProvider` uses `httpx.Client` against `https://integrate.api.nvidia.com/v1/chat/completions` (OpenAI-compatible) and `/embeddings`. Reranker hits the **separate** host `https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking`. `vision()` requires `model == "meta/llama-4-maverick-17b-128e-instruct"`. Sends image as base64 in OpenAI multimodal content array.
- Both providers receive a `TokenTracker` and call `tracker.record(...)` so the cost meter stays honest.

Add `httpx>=0.27` to [requirements.txt](requirements.txt) (already a transitive dep of `anthropic`; pin it).

### 2. Agent Layer — `ai/agents/` (function-based)

```
ai/agents/
  __init__.py             # AgentContext dataclass
  page_classifier.py      # classify_page(text, ctx) -> str
  vision_extractor.py     # extract_from_image(image_bytes, prompt, ctx) -> str
  text_extractor.py       # extract_from_text(text, prompt, ctx) -> list[dict]
  csi_classifier.py       # classify(description, fallback_keywords, ctx) -> tuple[str, float]
  description_normalizer.py # normalize(raw, sheet, keynote_ref, ctx) -> str
  rag.py                  # prime_normalizer(raw, ctx) -> list[str]
  orchestrator.py         # review_rows(rows, threshold, ctx) -> list[QTORow]
```

`AgentContext` carries `providers: dict[str, Provider]`, `tracker`, `cache`, `agent_config: dict` (this agent's slice of `config["agents"]`), and `rag_store: Optional[HistoricalStore]`. Agents are stateless; caching lives in `MultiAgentClient`.

The normalizer reuses `ai.description_composer._SYSTEM` verbatim — that prompt is model-portable; only the inference call changes.

### 3. Assembler Changes — Two Lines

Do **not** touch [core/assembler.py:59](core/assembler.py:59) `process_page()`. It only ever talks to `self._ai`, which is duck-typed. The dispatch happens upstream in [ui/main_window.py:88](ui/main_window.py:88):

```python
mode = self._config.get("extraction_mode", "hybrid")
if mode == "multi_agent":
    from ai.multi_agent_client import MultiAgentClient
    ai = MultiAgentClient(self._config, tracker)
else:
    ai = AIClient(self._config, tracker)
assembler = Assembler(self._config, ai, tracker)
```

Inside [core/assembler.py:325](core/assembler.py:325) `flush_batched_compose`, append a single hook for the orchestrator review (runs after Phase-7 batch flush, before sort/validate):

```python
review = getattr(self._ai, "review_low_confidence_rows", None)
if review is not None:
    threshold = self._config.get("confidence_review_threshold", 0.75)
    review(rows, threshold)
```

`process_page` and `_make_row` stay agnostic; they keep assigning method-based confidence at [assembler.py:281](core/assembler.py:281). The orchestrator revises `confidence` and (optionally) `description` in-place during the post-pass.

### 4. AIClient Refactor — Parallel Class, Not Facade

Leave [ai/client.py](ai/client.py) **exactly as-is**. Add [ai/multi_agent_client.py](ai/multi_agent_client.py) next to it.

Rationale: `AIClient` houses the working Phase-7 batch path (`_pending_compose`, `flush_pending_compose`), prompt-cache wiring, and `chat_over_rows`. Wrapping it in a facade risks breaking the 50% batch saving currently in production. A parallel class makes the multi-agent path additive and reversible (toggle `extraction_mode` back to `hybrid` at any time).

`MultiAgentClient` exposes the same 13-method public surface as `AIClient` plus a 14th: `review_low_confidence_rows`. Each method is a 1-line delegation to its agent. Phase-7 hooks (`cost_saver_mode`, `pending_compose_count`, `flush_pending_compose`) are stubbed as no-ops/`False`/`0` so `assembler.flush_batched_compose` doesn't crash on `getattr`.

`chat_over_rows` and `describe_diff_cluster` stay on Anthropic even in `multi_agent` mode — they need prompt caching for repeated questions and the diff prompt is tuned for Sonnet.

Add `review_low_confidence_rows` to **`AIClient` too** (~30 lines, routes to Sonnet) so `hybrid` mode also benefits from the orchestrator pass.

### 5. Config Schema (Additive)

All new keys; existing keys untouched. Append to [config.yaml](config.yaml):

```yaml
extraction_mode: hybrid     # extends existing enum: hybrid | claude_only | multi_agent

providers:
  anthropic:
    api_key_env: ANTHROPIC_API_KEY
  nvidia:
    api_key_env: NVIDIA_API_KEY
    chat_base_url: "https://integrate.api.nvidia.com/v1"
    rerank_base_url: "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking"
    timeout_s: 60

agents:
  page_classifier:
    provider: nvidia
    model: "nvidia/nemotron-mini-4b-instruct"
    temperature: 0.0
    max_tokens: 24
    fast_path_heuristics: true
  vision_extractor:
    provider: nvidia
    model: "meta/llama-4-maverick-17b-128e-instruct"
    temperature: 0.0
    max_tokens: 4000
    fallback_provider: anthropic
    fallback_model: claude-sonnet-4-6
  text_extractor:
    provider: nvidia
    model: "mistralai/mistral-nemotron"
    temperature: 0.0
    max_tokens: 2000
  csi_classifier:
    provider: nvidia
    model: "nvidia/nemotron-mini-4b-instruct"
    temperature: 0.0
    max_tokens: 64
  normalizer:
    provider: nvidia
    model: "nvidia/nemotron-mini-4b-instruct"
    temperature: 0.0
    max_tokens: 512
    use_rag_priming: true
    rag_top_k: 5
  orchestrator:
    provider: anthropic
    model: claude-sonnet-4-6
    temperature: 0.0
    max_tokens: 1500
    review_threshold: 0.75

rag:
  enabled: false             # opt-in
  embedding_model: "nvidia/nv-embed-v1"
  rerank_model: "nv-rerank-qa-mistral-4b:1"
  store_path: "./cache/historical.db"
```

`hybrid` and `claude_only` modes ignore `agents:`/`providers:`/`rag:` blocks entirely. `multi_agent` mode ignores the legacy `models:` block. Backward compat is preserved because `AIClient.__init__` still reads `models`/`anthropic_api_key`/`cost_saver_mode` from their original positions.

### 6. Orchestrator Review — End-of-Run Batched

`ai/agents/orchestrator.py` exposes `review_rows(rows, threshold, ctx)`:

1. Filter to `rows` where `not is_header_row and confidence < threshold`.
2. Chunk to ~20 rows per request.
3. For each chunk, send a JSON payload (`row_id`, `description`, `qty`, `units`, `sheet`, `method`, `confidence`) to Sonnet with a system prompt: "for each row, return one of {confirm, revise, reject} with a revised description if revising."
4. Apply revisions in-place: bump `confidence` to 0.9, set `extraction_method = "reviewed"`, update `description` if revised.

Hook fires from [core/assembler.py:325](core/assembler.py:325) `flush_batched_compose` after Phase-7 flush, before sort/validate. Cost: one Sonnet call per ~20 low-conf rows — typically a single-digit number of calls per PDF.

### 7. RAG Store — SQLite + numpy Embeddings

[core/rag_store.py](core/rag_store.py) (new):

```sql
CREATE TABLE historical_descriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_input TEXT NOT NULL,
    normalized TEXT NOT NULL,
    sheet TEXT, keynote_ref TEXT,
    project_name TEXT,
    embedding BLOB NOT NULL,           -- np.float32 bytes
    created_at TEXT DEFAULT (datetime('now')),
    used_count INTEGER DEFAULT 0
);
CREATE INDEX idx_hist_proj ON historical_descriptions(project_name);
```

`HistoricalStore` exposes `add(raw, normalized, sheet, project, embedding)` and `search(query_embedding, top_k=20, project=None) -> list[(score, dict)]` doing in-Python cosine similarity. Avoid `sqlite-vec` (fragile macOS install); linear scan over <50k rows is sub-millisecond.

**Population is opt-in** — adding every extraction creates a feedback loop where bad rows poison future runs. Phase 1: CLI populator only. Phase 2 (separate ticket): "Save approved rows" button in `ui/results_table.py`.

**Integration is priming, not validation.** Inside `description_normalizer.normalize`: if `ctx.rag_store and use_rag_priming`, embed the raw text → search top-20 → rerank with `nv-rerank-qa-mistral-4b:1` → keep top-K → inject as additional few-shot examples appended to the existing `_SYSTEM` prompt. All wrapped in `try/except ProviderCapabilityError: pass` so RAG failure doesn't fail extraction.

## Critical Files To Modify

| File | Change |
|---|---|
| [requirements.txt](requirements.txt) | Add `httpx>=0.27` |
| [config.yaml](config.yaml) | Append `providers:`, `agents:`, `rag:` blocks; extend `extraction_mode` enum |
| [ai/providers/base.py](ai/providers/base.py) | NEW — Protocol + capability error |
| [ai/providers/anthropic_provider.py](ai/providers/anthropic_provider.py) | NEW — wraps existing `_call`/`_vision_call` logic |
| [ai/providers/nvidia_provider.py](ai/providers/nvidia_provider.py) | NEW — httpx client, OpenAI-compatible chat + embeddings + separate rerank URL |
| [ai/agents/__init__.py](ai/agents/__init__.py) | NEW — `AgentContext` dataclass |
| [ai/agents/page_classifier.py](ai/agents/page_classifier.py) | NEW |
| [ai/agents/vision_extractor.py](ai/agents/vision_extractor.py) | NEW |
| [ai/agents/text_extractor.py](ai/agents/text_extractor.py) | NEW |
| [ai/agents/csi_classifier.py](ai/agents/csi_classifier.py) | NEW |
| [ai/agents/description_normalizer.py](ai/agents/description_normalizer.py) | NEW — reuses `description_composer._SYSTEM` |
| [ai/agents/rag.py](ai/agents/rag.py) | NEW |
| [ai/agents/orchestrator.py](ai/agents/orchestrator.py) | NEW |
| [ai/multi_agent_client.py](ai/multi_agent_client.py) | NEW — 14-method facade matching `AIClient` surface |
| [ai/client.py](ai/client.py) | Add `review_low_confidence_rows` method (~30 lines) |
| [core/rag_store.py](core/rag_store.py) | NEW — SQLite + numpy cosine search |
| [core/assembler.py](core/assembler.py) | Append 3-line orchestrator hook in `flush_batched_compose` (line 325) |
| [core/token_tracker.py](core/token_tracker.py) | Add NVIDIA model buckets to `_PRICING`, add `record_nvidia()` |
| [ui/main_window.py](ui/main_window.py) | Branch on `extraction_mode` at line 88 to instantiate `MultiAgentClient` |

## Files To Reuse (No Modification)

- [ai/description_composer.py](ai/description_composer.py) `_SYSTEM` — model-portable, used verbatim by normalizer agent
- [ai/batch_runner.py](ai/batch_runner.py) — Anthropic batch path stays for `hybrid`+cost-saver
- [parser/pdf_splitter.py](parser/pdf_splitter.py) — heuristics remain as fast-path; new agent fires only on default fallback
- [core/qto_row.py](core/qto_row.py) — `QTORow` already has `confidence`, `needs_review`, `extraction_method` fields
- [core/validator.py](core/validator.py) — threshold enforcement is generic; already reads `confidence_review_threshold`

## Commit Sequence (6 commits)

1. **`feat(providers): Provider protocol + Anthropic/NVIDIA impls`** — providers package, `httpx` pin, `tests/test_providers.py`. No production wiring.
2. **`feat(agents): function-based agents for the five extraction stages`** — agents package, `AgentContext`, `tests/test_agents.py`. No production wiring.
3. **`feat(client): MultiAgentClient + orchestrator review hook`** — `multi_agent_client.py`, `orchestrator.py`, `review_low_confidence_rows` on `AIClient`, 3-line hook in `assembler.flush_batched_compose`, dispatch in `ui/main_window.py`, `tests/test_multi_agent_client.py`.
4. **`feat(rag): historical store + priming integration`** — `core/rag_store.py`, `ai/agents/rag.py`, normalizer reads RAG when `use_rag_priming`. Default-off.
5. **`feat(config): multi_agent mode + providers/agents/rag config blocks`** — additive `config.yaml` keys, integration test against `tests/fixtures/HBT_drawings.pdf` with mocked NVIDIA HTTP.
6. **`feat(meter): NVIDIA token-tracker buckets + cost meter labels`** — extend `_PRICING`, add `record_nvidia`, update `ui/cost_meter.py`.

After commit 3 the new path is selectable in code; after commit 5 it's user-selectable via config. You can ship 1–3 first and decide whether to land 4–6 based on observed quality.

## Verification

**Unit (per commit):**
- `tests/test_providers.py` — mock `httpx`, assert NVIDIA payload shape, assert reranker hits the *different* base URL, assert capability errors raise correctly.
- `tests/test_agents.py` — fake `Provider` returning canned strings; assert each agent parses output correctly into typed result.
- `tests/test_multi_agent_client.py` — instantiate with both providers mocked; call all 14 methods; assert routing matches config.

**Integration:**
- `tests/test_multi_agent_integration.py` — runs `Assembler.process_page` against `tests/fixtures/HBT_drawings.pdf` with `extraction_mode: multi_agent` and a mocked NVIDIA HTTP layer. Assertions:
  - Row count regression: `abs(len(rows_multi_agent) - len(rows_hybrid)) <= 0.10 * len(rows_hybrid)` — within ±10%.
  - Confidence histogram: `sum(1 for r in rows if r.confidence >= 0.75) / len(rows) >= 0.60` — at least 60% above review threshold.
  - **Do not** assert exact descriptions — different models will produce different exact text.

**Live smoke (manual, gated by env var):**
- `pytest.mark.skipif(not os.environ.get("NVIDIA_API_KEY"), ...)` test that hits real NVIDIA `/chat/completions` with `nemotron-mini-4b` on a one-line input. Catches "URL/auth/model name moved" before users find it.

**Manual end-to-end:**
1. Set `NVIDIA_API_KEY` in env.
2. Set `extraction_mode: multi_agent` in `config.yaml`.
3. Run `python main.py`, open `tests/fixtures/HBT_drawings.pdf`.
4. Verify: cost meter shows NVIDIA token usage, low-confidence rows get reviewed, exported XLSX matches the GC template structure.
5. Toggle back to `extraction_mode: hybrid`, re-run same PDF, confirm output is identical to pre-refactor (regression check).

## Open Question (Defer)

**Orchestrator timing — per-page or end-of-run batched?** This plan picks **end-of-run** (reuses `flush_batched_compose` hook, costs one batch per PDF, zero churn to `process_page`). Per-page would give Claude fresher context but requires touching `process_page` and breaks the "smallest diff" goal. If row quality after end-of-run review is poor, revisit and add a per-page hook in `_make_row`.
