"""FastAPI shell for the Zeconic QTO web app.

Mirrors the architecture in /Users/zain/CPM_APP/backend/main.py:
* Loads ``backend/.env`` (or falls back to repo-root ``.env``) so API keys
  configured at the project root keep working without duplication.
* Adds the parent directory to ``sys.path`` so the existing ``ai/``,
  ``core/``, ``parser/``, ``cv/`` packages can be imported by routes
  without restructuring the codebase.
* Serves the built ``frontend/dist`` SPA in production (single-port mode).
* In development (``APP_ENV=development``) only enables CORS for the Vite
  dev server on :5173 — frontend is served from there, this process only
  handles ``/api/*`` routes.

The route surface starts intentionally small (``/api/health``,
``/api/info``, ``/api/extraction-modes``) — concrete extraction routes
land in follow-up commits as the wiring against ``core.assembler`` /
``ai.multi_agent_client`` matures.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ── Path / env bootstrap ────────────────────────────────────────────────
_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent

# Add repo root to sys.path so `from ai.client import AIClient` etc. work.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load env from backend/.env first (matches CPM); fall back to repo-root .env
# (matches the existing PyQt6 main.py behaviour) so the user's API keys flow
# through without re-pasting. ``override=True`` matches CPM's main.py so the
# .env file is the source of truth — a stale empty shell variable can't
# silently shadow the real key.
for _env_candidate in (_BACKEND_DIR / ".env", _REPO_ROOT / ".env"):
    if _env_candidate.is_file():
        load_dotenv(_env_candidate, override=True)


from fastapi import FastAPI  # noqa: E402  (must come after sys.path mutation)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402


APP_ENV = os.environ.get("APP_ENV", "production").lower()

# QTO dev defaults are :5142 (frontend) and :8042 (backend) so they
# coexist with the CPM dev stack on the same machine.
_DEV_ORIGINS = [
    "http://localhost:5142", "http://127.0.0.1:5142",
    # Vite occasionally picks the next free port; allow common alternates.
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:5174", "http://127.0.0.1:5174",
]
_PROD_ORIGINS = ["http://localhost:8765", "http://127.0.0.1:8765"]
_CORS_ORIGINS = _DEV_ORIGINS if APP_ENV == "development" else _PROD_ORIGINS


def _resolve_dist_dir() -> Path:
    """Return the SPA dist directory if it exists, else None.

    The frontend may not be built yet during early development. We don't
    crash — `/api/*` works regardless, and a nicer 503 fires if the user
    hits the SPA root before running ``npm --prefix frontend run build``.
    """
    override = os.environ.get("ZECONIC_STATIC_ROOT", "").strip()
    if override:
        return Path(override)
    return _REPO_ROOT / "frontend" / "dist"


_DIST_DIR = _resolve_dist_dir()


# ── App construction ────────────────────────────────────────────────────

app = FastAPI(
    title="Zeconic QTO",
    description=(
        "Web API for the Zeconic Quantity Takeoff tool. Wraps the existing "
        "multi-agent extraction pipeline (ai/, core/, parser/, cv/) and "
        "serves the Vite-built React SPA in production."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── /api routes ─────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    """Liveness check used by `scripts/start-qto.sh` to wait for boot."""
    return {"status": "ok", "env": APP_ENV}


@app.get("/api/info")
def info() -> dict:
    """Bootstrap payload the SPA reads on first paint.

    Returns the current extraction mode (so the topbar badge can render
    server-side truth instead of guessing from local state) plus a
    minimal feature flag block. Reads the same ``config.yaml`` the
    PyQt6 desktop app uses so the two surfaces share state.
    """
    import yaml

    cfg_path = _REPO_ROOT / "config.yaml"
    cfg = {}
    if cfg_path.is_file():
        with cfg_path.open() as f:
            cfg = yaml.safe_load(f) or {}

    return {
        "extraction_mode": cfg.get("extraction_mode", "hybrid"),
        "ui_v2": bool(cfg.get("ui_v2", False)),
        "cost_saver_mode": bool(cfg.get("cost_saver_mode", False)),
        "has_anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY", "")),
        "has_nvidia_key": bool(os.environ.get("NVIDIA_API_KEY", "")),
        "version": "0.1.0",
    }


@app.get("/api/extraction-modes")
def extraction_modes() -> dict:
    """Static catalogue of the three extraction modes shown in the UI.

    Mirrors ``ui/views/main_window.py::_EXTRACTION_MODES`` so the web UI
    and the desktop UI offer the same picker. The desktop app is the
    canonical source — keep this list aligned by hand for now; an import
    refactor is a later task.
    """
    return {
        "modes": [
            {
                "key": "hybrid",
                "label": "Hybrid (Claude)",
                "description": (
                    "Claude handles classification, vision, and composition. "
                    "Most accurate; highest token spend."
                ),
            },
            {
                "key": "multi_agent",
                "label": "Multi-Agent (NVIDIA + Claude)",
                "description": (
                    "NVIDIA NIM agents do extraction; Claude only reviews "
                    "rows below the confidence threshold. Lowest cost."
                ),
            },
            {
                "key": "claude_only",
                "label": "Claude Only (legacy)",
                "description": (
                    "Pure Claude pipeline, no agent routing. Use only to "
                    "bisect issues seen in multi-agent or hybrid modes."
                ),
            },
        ],
    }


# ── SPA fallthrough (production single-port mode) ──────────────────────

if _DIST_DIR.is_dir() and (_DIST_DIR / "index.html").is_file():
    # Serve hashed asset bundles directly. ``html=False`` so /api/* paths
    # aren't shadowed by index.html before they hit FastAPI's routes.
    app.mount(
        "/assets",
        StaticFiles(directory=_DIST_DIR / "assets"),
        name="assets",
    )

    @app.get("/")
    def _index() -> FileResponse:
        return FileResponse(_DIST_DIR / "index.html")

    @app.get("/{path:path}")
    def _spa_fallback(path: str) -> FileResponse:
        """SPA history-fallback — every non-/api path returns index.html."""
        if path.startswith("api/"):
            # FastAPI should have already matched this; double-check is
            # belt-and-suspenders against future route ordering bugs.
            raise FileNotFoundError(path)
        candidate = _DIST_DIR / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST_DIR / "index.html")
