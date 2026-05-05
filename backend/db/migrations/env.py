"""Alembic env.py — async-ready, pulls connection string from Settings.

Adapted from the SQLAlchemy 2.0 Alembic async cookbook. Two execution
modes:

* **online** (the default for ``alembic upgrade head``): opens a real
  connection via ``asyncpg`` and runs migrations transactionally.
* **offline**: emits SQL to stdout without touching the database. Used
  for review / archival; we don't rely on it for prod deploys.

The URL is **not** read from ``alembic.ini`` — instead we ask
``backend.config.get_settings()``, which layers in the same ``.env``
files the FastAPI app uses. This keeps a single source of truth.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Add repo root to sys.path so ``from backend.db.models import Base`` resolves
# even when alembic is invoked from a non-root cwd.
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.config import get_settings  # noqa: E402
from backend.db.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the placeholder URL from alembic.ini with the real one.
_settings = get_settings()
config.set_main_option("sqlalchemy.url", _settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a database."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Open an async engine, run sync migrations on a sync connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
