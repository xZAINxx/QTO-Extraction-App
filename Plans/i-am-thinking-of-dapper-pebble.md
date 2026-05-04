# QTO Web — Multi-Tenant Feature Wiring

## Context

PR #2 shipped the QTO web stack scaffold (Vite + FastAPI mirroring CPM). The current frontend renders a Takeoff workspace, side nav, topbar, and a backend health card — but four named pieces are intentionally stubbed:

1. **Upload PDF** button is visual only — no ingest route, no extraction trigger.
2. **Mode badge** in the topbar shows `MULTI_AGENT` but isn't clickable on the web (it is on PyQt6).
3. **What Changed / Cockpit / Coverage** workspace tabs are deliberate "coming next" cards rather than ports of the PyQt6 widgets.
4. **Cost / token meters** are deferred entirely (the original critique flagged them as debugging UI in the bottom strip).

The user wants those wired in **AND** wants the same codebase deployable as a hosted multi-tenant SaaS (alongside the local desktop app, which stays in PyQt6 and is untouched). Decisions captured via AskUserQuestion this turn:

| Question | Decision |
|---|---|
| Phasing | **Multi-tenant from day 1** |
| Auth + storage + DB | **Supabase (one vendor)** — switched from Clerk because Clerk is auth-only; we need object storage too. Supabase consolidates auth + Postgres + S3-compatible storage under one project. |
| PDF storage | **Persistent per-project workspace** (Supabase Storage bucket, server-side signed URLs) |
| Implementation cadence | **Parallel sub-agents** for independent commits (frontend ↔ backend can land in parallel) |

The PyQt6 desktop app continues to work as-is (local config.yaml, local cache/). The web app is a separate surface with its own state store; the two share the same `core/`, `ai/`, `parser/`, `cv/` modules and the same `requirements.txt`.

---

## Pipeline Reality Check (From Exploration)

The Python pipeline is **NOT** designed for the FastAPI async model. From `core/rag_store.py:42` and `ai/client.py:84-87`:

- `HistoricalStore` SQLite connection is single-threaded by design.
- `AIClient._compose_cache` and `_classify_cache` are non-thread-safe dicts.
- `Assembler.process_page` (`core/assembler.py:59`) calls `fitz.open` and Anthropic API calls synchronously — it blocks for seconds per page.

Concrete consequences for the web design:

- Wrap every extraction call in `asyncio.to_thread(...)` so it runs on the threadpool, not the event loop.
- One extraction per user at a time. A per-user `asyncio.Lock` + a global semaphore of 3 concurrent jobs.
- One `MultiAgentClient` / `AIClient` instance **per extraction job** (do not share across jobs — caches will collide). Construct fresh per job.
- One `HistoricalStore` connection per job; close on completion. RAG seed data stays read-mostly so cross-job reads are fine.

`QTORow` is fully JSON-serializable (`core/qto_row.py:4-44`), no datetime/numpy fields → safe for `dataclasses.asdict()` over the wire.

`TokenTracker.on_update` (`core/token_tracker.py:120-174`) fires synchronously per API call — drain it via `asyncio.Queue` and forward as SSE.

---

## Architecture

```
┌─── frontend/ (Vite + React 19) ──────────────────────────────────┐
│  <SupabaseProvider> → useSession() → fetch /api/* with Bearer    │
│  zustand stores: project, extraction-job, rows, cost-stream      │
│  Components:                                                     │
│    UploadDropzone, ModePickerMenu, CostPopover                   │
│    workspaces/{Takeoff, WhatChanged, Cockpit, Coverage}.jsx      │
└──────────────────────────────────────────────────────────────────┘
                            │  /api/* (Vite proxy in dev,
                            │   single-port in prod)
                            ▼
┌─── backend/ (FastAPI + uvicorn) ─────────────────────────────────┐
│  middleware/auth.py  Verify Supabase JWT (jwks public-key check) │
│                      → request.state.user (DB row)               │
│  routes/{me, projects, uploads, extractions, rows,               │
│          extraction_modes, costs, diff, cockpit, coverage}       │
│  services/                                                        │
│    storage.py     SupabaseStorage (default) | LocalDiskStorage   │
│    jobs.py        JobRunner (asyncio + per-user lock + semaphore)│
│    sse.py         AsyncQueue → text/event-stream                 │
│  db/                                                              │
│    models.py (SQLAlchemy 2.0)                                    │
│    migrations/   (Alembic)                                        │
│  domain (untouched, imported as-is): ai/, core/, parser/, cv/    │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─── Supabase Project ─────────────────────────────────────────────┐
│  Auth   — email/magic-link + OAuth (Google/GitHub)               │
│  DB     — Postgres (users · projects · pdfs · extractions ·      │
│           qto_rows · token_events)                                │
│  Storage— bucket "qto-pdfs" with RLS keyed on user_id            │
│  Local  — `supabase start` runs all three locally via Docker     │
│  Prod   — managed Supabase (free tier → Pro $25/mo at growth)    │
└──────────────────────────────────────────────────────────────────┘
```

---

## Decisions (Recommended Path)

### 1. Auth — Supabase Auth

- **Frontend**: `@supabase/supabase-js` + `@supabase/auth-ui-react` for the sign-in card. `useSession()` provides the JWT; injected into every `fetch('/api/*', { headers: Authorization: 'Bearer ${jwt}' })`. Magic-link email + Google OAuth enabled by default; password as fallback.
- **Backend**: `supabase-py` for storage SDK; for auth we verify Supabase-issued JWTs locally via `pyjwt` against the project's JWKS endpoint — no extra round-trip per request. Middleware `middleware/auth.py` resolves the JWT's `sub` claim → SQLAlchemy `User` row (lazy-provision on first sight). FastAPI dependency `Depends(current_user)` gates every protected route.
- **Env vars** (single Supabase project covers all three subsystems):
  - `SUPABASE_URL` (e.g. `https://xxxx.supabase.co`)
  - `SUPABASE_ANON_KEY` — public; safe to ship in the frontend bundle.
  - `SUPABASE_SERVICE_ROLE_KEY` — server-only; bypasses RLS for backend storage operations.
  - `SUPABASE_JWT_SECRET` — JWT signing secret for local verification.
  - Frontend reads `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY`.

### 2. Storage — Supabase Storage (with pluggable abstraction)

`backend/services/storage.py` defines:

```python
class Storage(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def local_path(self, key: str) -> Path: ...   # for fitz.open
    def delete(self, key: str) -> None: ...
    def signed_url(self, key: str, expires_in: int = 3600) -> str: ...
```

Two implementations:
- `SupabaseStorage(client)` — **default for both dev and prod**. One bucket `qto-pdfs` with RLS policy `(auth.uid()::text = (storage.foldername(name))[1])` so users can only read their own folder. `local_path()` downloads to `/tmp/qto-{key.replace('/', '-')}` for `fitz.open`, deletes after extraction.
- `LocalDiskStorage(root: Path)` — for self-hosted users running without Supabase. Same interface; selected via `STORAGE_BACKEND=local`.

Bucket-key convention: `{user_id}/{project_id}/{pdf_id}/source.pdf`. Storage RLS + the SQL FK chain together provide defense-in-depth.

### 3. Database — Supabase Postgres + SQLAlchemy 2.0 + Alembic

Supabase ships a managed Postgres. Locally, `supabase start` boots the whole stack (Postgres + Auth + Storage + Studio UI) in Docker. We connect from FastAPI via the connection-pooled `pgbouncer` URL Supabase exposes as `SUPABASE_DB_URL` — same SQLAlchemy code in dev and prod.

Tables (full DDL in §"Schema" below):
- `users` — Clerk subject ID + per-user prefs (extraction_mode default).
- `projects` — name, deadline, markup defaults (for Cockpit), owner.
- `pdfs` — filename, storage_key, page_count, byte_size, project_id.
- `extractions` — status (pending|running|completed|failed|canceled), extraction_mode snapshot, cost_usd, started/finished timestamps, pdf_id.
- `qto_rows` — full `QTORow` flattened, FK to extraction.
- `token_events` — append-only log per `tracker.on_update`. Drives both the cost popover and historical billing.
- `coverage_assertions` (Phase 2) — per-project sheet scope status.

`backend/db/session.py` — async SQLAlchemy engine + `get_db()` FastAPI dependency.
`backend/db/migrations/` — Alembic. First migration creates all tables.

### 4. Job runner — asyncio + per-user lock

`backend/services/jobs.py`:

```python
_USER_LOCKS: dict[UUID, asyncio.Lock] = {}
_GLOBAL_SEMAPHORE = asyncio.Semaphore(3)   # tunable via MAX_CONCURRENT_JOBS

async def run_extraction(extraction_id: UUID, user_id: UUID) -> None:
    lock = _USER_LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock, _GLOBAL_SEMAPHORE:
        await asyncio.to_thread(_blocking_run, extraction_id)
```

`_blocking_run` is essentially `extraction_worker.run()` adapted: builds its own `TokenTracker`, picks `MultiAgentClient` vs `AIClient` per `extraction.extraction_mode`, calls `Assembler.process_page` in a loop, persists rows + token events to Postgres after each page.

Progress is published via `asyncio.Queue` (one per extraction_id) so the SSE endpoint can `await queue.get()`.

This pattern survives a single uvicorn worker. Multi-worker requires Redis pub/sub for the queue (out of scope for v1; document as a known scaling limit).

### 5. Frontend state — zustand + react-query

- `zustand` for ephemeral UI state (active workspace, selected project, modal toggles).
- `@tanstack/react-query` for server cache (projects, rows, extraction status). Invalidate on SSE events.
- SSE hook: `useExtractionStream(extractionId)` opens an `EventSource`, emits typed events, dispatches to react-query cache.

---

## Schema

Concise — full Alembic migration generates these:

```sql
-- ``id`` matches Supabase Auth ``auth.users.id`` so RLS policies can use ``auth.uid()``.
CREATE TABLE users (
  id UUID PRIMARY KEY,                        -- = supabase auth.users.id (no FK to auth schema)
  email TEXT,
  extraction_mode TEXT NOT NULL DEFAULT 'multi_agent',
  cost_saver_mode BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  deadline TIMESTAMPTZ,
  markup_overhead FLOAT DEFAULT 10,
  markup_profit FLOAT DEFAULT 8,
  markup_contingency FLOAT DEFAULT 5,
  exclusions TEXT[] DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX projects_user_idx ON projects(user_id);

CREATE TABLE pdfs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  storage_key TEXT NOT NULL,
  page_count INT,
  byte_size BIGINT NOT NULL,
  fingerprint TEXT NOT NULL,
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX pdfs_project_idx ON pdfs(project_id);

CREATE TABLE extractions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pdf_id UUID NOT NULL REFERENCES pdfs(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending',
  extraction_mode TEXT NOT NULL,
  cost_saver_mode BOOLEAN NOT NULL DEFAULT FALSE,
  cost_usd FLOAT NOT NULL DEFAULT 0,
  total_tokens INT NOT NULL DEFAULT 0,
  api_calls INT NOT NULL DEFAULT 0,
  error_message TEXT,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX extractions_user_status_idx ON extractions(user_id, status);

CREATE TABLE qto_rows (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  extraction_id UUID NOT NULL REFERENCES extractions(id) ON DELETE CASCADE,
  position INT NOT NULL,                  -- ordering within sheet
  s_no INT,
  tag TEXT,
  drawings TEXT,
  details TEXT,
  description TEXT,
  qty FLOAT,
  units TEXT,
  unit_price FLOAT,
  total_formula TEXT,
  math_trail TEXT,
  trade_division TEXT,
  source_page INT,
  source_sheet TEXT,
  extraction_method TEXT,
  confidence FLOAT,
  bbox JSONB,
  is_header_row BOOLEAN DEFAULT FALSE,
  confirmed BOOLEAN DEFAULT FALSE,
  needs_review BOOLEAN DEFAULT FALSE,
  risk_flags TEXT[] DEFAULT '{}'
);
CREATE INDEX qto_rows_extraction_idx ON qto_rows(extraction_id);
CREATE INDEX qto_rows_division_idx ON qto_rows(extraction_id, trade_division);

CREATE TABLE token_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  extraction_id UUID NOT NULL REFERENCES extractions(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  api_calls INT NOT NULL,
  input_tokens INT NOT NULL,
  output_tokens INT NOT NULL,
  cache_read_tokens INT NOT NULL DEFAULT 0,
  cache_write_tokens INT NOT NULL DEFAULT 0,
  cost_usd FLOAT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX token_events_extraction_idx ON token_events(extraction_id);
```

---

## API Surface (final)

All routes under `/api/*` are auth-required except `/api/health` and `/api/webhooks/clerk`.

| Route | Method | Purpose |
|---|---|---|
| `/api/health` | GET | (existing) |
| `/api/info` | GET | (existing — extend with `user.extraction_mode`) |
| `/api/me` | GET | current user profile + prefs |
| `/api/me/extraction-mode` | POST | set per-user mode preference |
| `/api/me/cost-saver` | POST | toggle cost-saver mode |
| `/api/projects` | GET, POST | list / create projects |
| `/api/projects/{id}` | GET, PATCH, DELETE | per-project ops (name, deadline, markup defaults) |
| `/api/projects/{id}/pdfs` | POST | multipart PDF upload, returns `{pdf_id, page_count}` |
| `/api/projects/{id}/pdfs` | GET | list project PDFs |
| `/api/pdfs/{id}` | DELETE | remove a PDF + its extractions |
| `/api/extractions` | POST | start an extraction `{pdf_id, extraction_mode?}` |
| `/api/extractions/{id}` | GET | extraction status + summary |
| `/api/extractions/{id}/cancel` | POST | flip status to canceled |
| `/api/extractions/{id}/rows` | GET | paginated rows (cursor-based) |
| `/api/extractions/{id}/events` | GET (SSE) | live progress + token + row stream |
| `/api/extractions/{id}/cost` | GET | aggregated token + cost breakdown by model |
| `/api/extractions/{id}/coverage` | GET | division coverage + missing-sheet roster |
| `/api/extractions/{id}/cockpit` | GET | division totals, sub-bid breakdown |
| `/api/extractions/{id}/diff/{compare_id}` | GET | set-diff result vs another extraction |
| `/api/rows/{id}` | PATCH | flip `confirmed` / `needs_review`, edit description |

SSE event types on `/extractions/{id}/events`:
- `progress` — `{page, total, page_type}`
- `row_ready` — `{rows: [QTORow, ...]}`
- `tokens` — full `TokenUsage` snapshot
- `batch_status` — for cost-saver flush
- `error` — `{message}`
- `done` — `{cost_usd, total_rows}`
- terminator: `data: [DONE]\n\n`

---

## Frontend Surface

### New components (under `frontend/src/`)

| Component | Path | Purpose |
|---|---|---|
| `auth/SignInGate.jsx` | shell | Wraps content with Supabase session check; renders `<Auth>` UI when signed-out |
| `auth/supabaseClient.js` | shared | Single shared `createClient()` instance using `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY` |
| `panels/UploadDropzone.jsx` | takeoff | drag-drop + click upload, multipart POST, opt-out of "auto-start extraction" checkbox |
| `panels/ProjectSwitcher.jsx` | topbar | replaces `Untitled Project ▾` static button — list of user's projects + new-project modal |
| `components/ModePickerMenu.jsx` | topbar | port of PyQt6 `_ClickableModeBadge` — opens menu, calls `/api/me/extraction-mode` |
| `panels/CostPopover.jsx` | settings (gear icon) | live cost from SSE stream, per-model breakdown, cost-saver toggle |
| `workspaces/WhatChangedWorkspace.jsx` | tab #2 | port of `ui/workspaces/diff_workspace.py` |
| `workspaces/CockpitWorkspace.jsx` | tab #3 | port of `ui/workspaces/cockpit_workspace.py` |
| `workspaces/CoverageWorkspace.jsx` | tab #4 | port of `ui/workspaces/coverage_workspace.py` |
| `hooks/useExtractionStream.js` | shared | EventSource wrapper, dispatches to react-query cache |
| `hooks/useApi.js` | shared | wraps fetch with Clerk Bearer token + JSON envelope |
| `stores/projectStore.js` | shared | zustand: active project + active extraction id |

### Files to modify

| File | Change |
|---|---|
| `frontend/src/App.jsx` | wrap with `<SignInGate>`, replace static topbar mode badge with `ModePickerMenu`, wire workspaces to real components, add CostPopover trigger to topbar |
| `frontend/src/main.jsx` | construct shared Supabase client at boot |
| `frontend/index.html` | (no change) |
| `frontend/package.json` | add: `@supabase/supabase-js`, `@supabase/auth-ui-react`, `@supabase/auth-ui-shared`, `@tanstack/react-query`, `react-dropzone` |

---

## Backend File Layout

```
backend/
  main.py                    (extend — wire routers, middleware)
  config.py                  NEW — pydantic-settings env loader
  db/
    __init__.py
    session.py               NEW — async engine + get_db dependency
    models.py                NEW — SQLAlchemy 2.0 models matching schema
    migrations/
      env.py
      versions/
        0001_initial.py      NEW — full schema
  middleware/
    auth.py                  NEW — SupabaseAuthMiddleware (verifies JWT via JWKS, lazy-provisions User row)
  services/
    storage.py               NEW — Storage Protocol + SupabaseStorage (default) + LocalDiskStorage
    jobs.py                  NEW — JobRunner with locks + queue
    sse.py                   NEW — async event-stream helper
    extraction_runner.py     NEW — adapted from ui/controllers/extraction_worker.py
                                    (no Qt; emits to asyncio.Queue + Postgres rows)
    coverage.py              NEW — division-coverage aggregation (matches ui/workspaces/coverage_workspace.py logic)
    cockpit.py               NEW — division-totals + markup math (matches ui/workspaces/cockpit_workspace.py)
    set_diff.py              NEW — wraps existing core/set_diff.py (already exists)
  routes/
    me.py                    NEW
    projects.py              NEW
    pdfs.py                  NEW
    extractions.py           NEW
    rows.py                  NEW
    extraction_modes.py      NEW (the existing /api/extraction-modes goes here)
    health.py                NEW (extracted)
  requirements.txt           (extend — sqlalchemy, alembic, asyncpg, pyjwt[crypto], supabase, pydantic-settings)
```

---

## Existing Code to Reuse Without Modification

| Module | Why |
|---|---|
| `core/assembler.py` | Process_page is the extraction core; extraction_runner calls it directly. |
| `core/qto_row.py` | The wire format. Use `dataclasses.asdict()`. |
| `core/token_tracker.py` | The on_update callback works as-is. Hook it to an asyncio.Queue. |
| `core/cache.py` (ResultCache) | Use as a per-extraction memoization layer keyed by storage_key. Stays local to the worker. |
| `core/set_diff.py` | Diff workspace API wraps this verbatim. |
| `core/rag_store.py` | RAG queries unchanged; per-job connection. |
| `ai/multi_agent_client.py` | Construct fresh per job. |
| `ai/client.py` | Construct fresh per job. |
| `parser/pdf_splitter.py:split_and_classify` | Unchanged. |
| `parser/zone_segmenter.py` | Unchanged (used by Coverage workspace). |
| `parser/callout_detector.py` | Unchanged (Phase 2 detail-bubble feature). |

---

## Existing Code to Reference for Logic Parity

| New web file | PyQt6 source of truth |
|---|---|
| `services/cockpit.py` | `ui/workspaces/cockpit_workspace.py:117-379` — division subtotals, markup math (additive not compound) |
| `services/coverage.py` | `ui/workspaces/coverage_workspace.py:83-240` — CSI division coverage, missing-sheet roster |
| `services/set_diff.py` | `ui/workspaces/diff_workspace.py:45-340` — open_compare flow, $-impact column |
| `services/extraction_runner.py` | `ui/controllers/extraction_worker.py:48-112` — drop the QThread shell, emit to asyncio.Queue |
| `components/ModePickerMenu.jsx` | `ui/views/main_window.py:50-71, 765-839` — `_EXTRACTION_MODES` constant + `_open_mode_menu` / `_set_extraction_mode` |
| `panels/UploadDropzone.jsx` | `ui/panels/upload_dialog.py` (legacy) — copy form-field labels |

---

## Commit Sequence (12 commits)

| # | Commit | What |
|---|---|---|
| 1 | `feat(web): bootstrap Supabase project + Alembic + SQLAlchemy 2.0 + first migration` | DB + models. `supabase start` for local dev (Postgres + Auth + Storage). Alembic migration creates app tables. |
| 2 | `feat(web): Supabase auth on FastAPI + React + protected /api/me` | SupabaseAuthMiddleware (JWKS verification), `<SignInGate>`, `/api/me` route. |
| 3 | `feat(web): pluggable storage abstraction (SupabaseStorage default + LocalDisk fallback)` | `services/storage.py` + tests. RLS policy on the `qto-pdfs` bucket. |
| 4 | `feat(web): projects + PDF upload routes + UploadDropzone UI` | `/api/projects`, `/api/projects/{id}/pdfs`. Upload UX in Takeoff. |
| 5 | `feat(web): extraction job runner with per-user lock + SSE stream` | `services/jobs.py`, `services/extraction_runner.py`, `/api/extractions`, `/api/extractions/{id}/events`. Wire Run-Extraction button. |
| 6 | `feat(web): rows API + virtualized DataTable port` | `/api/extractions/{id}/rows`, paginated React DataTable with status pills. |
| 7 | `feat(web): mode picker menu in topbar + per-user persistence` | `ModePickerMenu`, `/api/me/extraction-mode`. |
| 8 | `feat(web): What Changed workspace port` | `services/set_diff.py`, `/api/extractions/{id}/diff/{compare_id}`, `WhatChangedWorkspace.jsx`. |
| 9 | `feat(web): Cockpit workspace port` | `services/cockpit.py`, `/api/extractions/{id}/cockpit`, `CockpitWorkspace.jsx` with markup sliders. |
| 10 | `feat(web): Coverage workspace port` | `services/coverage.py`, `/api/extractions/{id}/coverage`, `CoverageWorkspace.jsx`. |
| 11 | `feat(web): cost popover with live SSE stream + cost-saver toggle` | `CostPopover.jsx`, `/api/extractions/{id}/cost`. |
| 12 | `feat(web): production deploy — Dockerfile + Fly.toml + single-port mount` | `Dockerfile` (multistage Node→Python), `fly.toml`, `scripts/start-qto.sh` (production), `alembic upgrade head` in entrypoint, README deploy section pointing at managed Supabase. |

Each commit is independently mergeable, lands behind passing CI, and doesn't break the desktop app or PR #2's existing routes.

### Parallel-agent dispatch plan (per the user's preference)

For each commit above, the implementation phase will dispatch up to 3 sub-agents in parallel where the work is independent:

| Commit | Agent A (backend) | Agent B (frontend) | Agent C (tests/docs) |
|---|---|---|---|
| 1 | Engineer — DB models + migration | — | Engineer — pytest fixtures + alembic test |
| 2 | Engineer — auth middleware | Engineer — SignInGate + supabaseClient | — |
| 3 | Engineer — storage Protocol + impls | — | Engineer — storage unit tests |
| 4 | Engineer — projects + uploads routes | Engineer — UploadDropzone + ProjectSwitcher | — |
| 5 | Engineer — extraction_runner + jobs | Engineer — useExtractionStream hook | — |
| 6 | Engineer — rows API + pagination | Engineer — DataTable port | — |
| 7 | Engineer — extraction_modes route | Engineer — ModePickerMenu | — |
| 8 | Engineer — set_diff service | Engineer — WhatChangedWorkspace | — |
| 9 | Engineer — cockpit service | Engineer — CockpitWorkspace | — |
| 10 | Engineer — coverage service | Engineer — CoverageWorkspace | — |
| 11 | Engineer — cost endpoint | Engineer — CostPopover | — |
| 12 | Engineer — Dockerfile + fly.toml | — | Engineer — README + CI deploy step |

Sequential dependencies that can NOT parallelise:
- Commit 1 must land before any other (every agent imports the DB models).
- Commit 2 must land before 4+ (every protected route uses the middleware).
- Commit 3 must land before 4 (uploads need storage).
- Commits 4 and 5 must land before 6–11 (everything reads `extractions` + `qto_rows`).

---

## Verification

### After every commit
- `npm run dev` boots both servers; existing `/api/health` + `/api/info` still respond.
- `pytest tests/` — the existing 312 tests still pass (none touch `backend/` or `frontend/`).
- Frontend smoke: load http://localhost:5142, sign in via Supabase Auth UI, see backend health card with all four green pills.

### Per-feature checks
1. **PDF upload + extraction (commits 4–6)**: drag a real Brooklyn fixture PDF onto the dropzone → see upload progress → row count grows live via SSE → Run Extraction completes with non-zero rows + cost.
2. **Mode picker (commit 7)**: click MULTI_AGENT badge → menu drops down → pick HYBRID → page reloads → badge shows HYBRID → next extraction uses Claude-only path.
3. **Workspaces (8–10)**: after one extraction completes, switch to Cockpit → markup sliders affect total live; Coverage → empty divisions visible; What Changed → upload a 2nd PDF, run, compare → diff workspace shows changed sheets.
4. **Cost popover (11)**: gear icon opens popover → during extraction, total ticks up live → cost-saver toggle persists across reloads.
5. **Deploy (12)**: `fly deploy` from a fresh checkout → app reachable at hosted URL → sign-in works → upload + extract works end-to-end.

### Production deploy gate
- Health check on `/api/health` from Fly's healthcheck config.
- Postgres migration runs at boot via `alembic upgrade head` in entrypoint (against the managed Supabase Postgres).
- Storage backend env-driven: `STORAGE_BACKEND=supabase` (default) or `local` for self-hosted.
- All secrets injected via `fly secrets set`: `ANTHROPIC_API_KEY`, `NVIDIA_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_DB_URL`.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **Pipeline is single-threaded** — `HistoricalStore`, `AIClient` caches collide under concurrent extractions. | Per-user `asyncio.Lock` + global `Semaphore(3)` in `services/jobs.py`. One `MultiAgentClient` per job. Document: `MAX_CONCURRENT_JOBS` is per-uvicorn-worker; multi-worker needs Redis pub/sub (out of v1 scope, flagged in commit 5 docstring). |
| **PDF storage bloat** — 50MB drawing sets × N users × M projects | R2 in production (zero egress cost). Soft per-user quota (500MB) enforced at upload route. Manual cleanup endpoint in commit 4. |
| **Supabase vendor lock** | All Supabase-specific code lives in `middleware/auth.py`, `services/storage.py::SupabaseStorage`, and `auth/SignInGate.jsx` + `auth/supabaseClient.js`. Swap-out is mechanical; swap-cost ≈ 2 days if needed (auth + storage are separate seams). The pluggable Storage Protocol means the LocalDisk backend keeps the app runnable without Supabase for self-hosters. |
| **Long extraction → SSE timeout** | Fly's HTTP idle timeout is 60s by default. Use Fly's WebSocket-style sticky sessions OR keep SSE alive with a heartbeat event every 30s. Document in commit 5. |
| **Migration drift between dev/prod** | Alembic migrations checked into the repo. CI runs `alembic upgrade head` on a temp Postgres before pytest. |
| **Cost-meter SSE replay after disconnect** | `/api/extractions/{id}/cost` on the popover open call returns the full current snapshot from `token_events` aggregation; SSE only adds deltas. Survives disconnects. |
| **PyQt6 desktop divergence** | Desktop and web use the same `core/`, `ai/`, `parser/` modules. Mode picker writes to `config.yaml` (desktop) vs `users.extraction_mode` (web) — different stores, no conflict. Documented in commit 7 docstring. |
| **Cold-start cost of `MultiAgentClient` per job** | One-time `~30ms` init; fine for jobs that run for minutes. If it ever matters, add a per-worker LRU pool keyed by `(extraction_mode, user_id)`. |

---

## Out of Scope (Explicit Non-Goals)

- **Background worker process** (Celery / RQ / Arq). v1 stays single-process; document the upgrade path.
- **Real-time collaboration** — multiple users editing the same project. Each project is single-owner in v1.
- **Excel export over the web** — desktop ships this; web port lands as a separate workstream once the read-only surface stabilises.
- **Inline RAG ghost-text suggestions** — XL effort, deferred per the original `dapper-pebble` plan.
- **Excel round-trip file watcher** — XL effort, deferred.
- **Multi-region deploy** — single Fly region (iad) for v1.

---

## Open Questions (Defer to Implementation)

- **Supabase free-tier limits** — 50,000 MAU, 500 MB DB, 1 GB storage on free; Pro at $25/mo lifts to 100,000 MAU, 8 GB DB, 100 GB storage. v1 fits comfortably in free tier; growth path is well-defined.
- **Bucket naming** — single `qto-pdfs` bucket with `{user_id}/{project_id}/{pdf_id}/source.pdf` key prefix + RLS keyed on the leading folder. Per-customer buckets only if a customer needs hard isolation (out of scope).
- **Cancellation semantics** — `POST /api/extractions/{id}/cancel` flips DB status, but the running thread may not yield until next page boundary. Document as "cancellation takes effect at next page".
- **Supabase RLS vs SQLAlchemy** — backend uses `SUPABASE_SERVICE_ROLE_KEY` (bypasses RLS) for performance; auth + scoping are enforced in our FastAPI middleware + WHERE-clauses, not via RLS. RLS only used on the storage bucket.
