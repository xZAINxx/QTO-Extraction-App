"""Typed settings loaded from environment / `.env` files.

Reads from (in order, with later values winning):
1. Repo-root `.env` — where ANTHROPIC_API_KEY / NVIDIA_API_KEY live (shared
   with the desktop app).
2. `backend/.env` — backend-specific overrides (DB url, Supabase keys).
3. Real environment variables — wins everywhere; production secrets
   injected via `fly secrets set` end up here.

The settings object is cached: ``get_settings()`` returns the same instance
on every call so importing it from anywhere is cheap. Tests can override
fields by constructing ``Settings(...)`` directly.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """All env-driven knobs in one place.

    Field order intentionally groups related concerns so a quick scan
    explains the surface. Required-vs-optional follows the same pattern
    the desktop app uses: API keys default to empty strings so the app
    starts cleanly without them and surfaces "Missing" in the UI rather
    than crashing on import.
    """

    # ── Runtime mode ────────────────────────────────────────────────
    app_env: Literal["development", "production", "test"] = "development"

    # ── Database ────────────────────────────────────────────────────
    # Default points at the local Docker Postgres in docker-compose.dev.yml.
    # Production overrides this with the Supabase pooled connection string.
    database_url: str = (
        "postgresql+asyncpg://qto:qto@127.0.0.1:5532/qto"
    )

    # ── Supabase (commits 2–3) ──────────────────────────────────────
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    # JWT signing secret used to verify access tokens from the frontend.
    # Pulled from Supabase project settings → API → JWT secret.
    supabase_jwt_secret: str = ""

    # ── Storage (commit 3) ──────────────────────────────────────────
    storage_backend: Literal["supabase", "local"] = "local"
    storage_local_root: Path = _REPO_ROOT / ".dev-storage"
    storage_bucket: str = "qto-pdfs"

    # ── AI providers (shared with desktop app) ──────────────────────
    anthropic_api_key: str = ""
    nvidia_api_key: str = ""

    # ── Job runner (commit 5) ───────────────────────────────────────
    max_concurrent_jobs: int = Field(default=3, ge=1, le=20)
    # Soft cap on per-user disk + storage usage. Enforced at upload time
    # in commit 4 to prevent a single user from filling the bucket.
    per_user_storage_quota_mb: int = Field(default=500, ge=10)

    # ── CORS ────────────────────────────────────────────────────────
    # The dev launcher uses :5142, but Vite occasionally roams to the
    # next free port; allow common alternates.
    cors_dev_origins: list[str] = [
        "http://localhost:5142",
        "http://127.0.0.1:5142",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]
    cors_prod_origins: list[str] = [
        "http://localhost:8765",
        "http://127.0.0.1:8765",
    ]

    model_config = SettingsConfigDict(
        env_file=(
            # Order matters — pydantic-settings layers them, last write wins.
            str(_REPO_ROOT / ".env"),
            str(_BACKEND_DIR / ".env"),
        ),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def is_prod(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins(self) -> list[str]:
        return self.cors_dev_origins if self.is_dev else self.cors_prod_origins


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide ``Settings`` instance (cached)."""
    return Settings()


__all__ = ["Settings", "get_settings"]
