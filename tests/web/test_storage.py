"""Unit tests for backend/services/storage.py — LocalDiskStorage only.

SupabaseStorage exercises the live SDK, so its tests are skipped unless
``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` are populated in the
environment. CI runs the local-disk path; integration tests against
real Supabase live in a separate workflow.

These tests run independently of the rest of the QTO test suite — they
don't import any PyQt6, fitz, or AI modules. ``pytest tests/web/`` is
the focused command; ``pytest tests/`` runs them alongside everything
else.
"""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

import pytest

from backend.services.storage import (
    LocalDiskStorage,
    Storage,
    StorageError,
    SupabaseStorage,
    get_storage,
    reset_storage,
)


# ── LocalDiskStorage ────────────────────────────────────────────────


@pytest.fixture
def local_root(tmp_path: Path) -> Path:
    """Per-test storage root — pytest's tmp_path fixture cleans it up."""
    return tmp_path / "storage"


@pytest.fixture
def local_storage(local_root: Path) -> LocalDiskStorage:
    return LocalDiskStorage(root=local_root)


def test_local_put_and_get_roundtrips(local_storage: LocalDiskStorage) -> None:
    payload = b"hello, drawings"
    local_storage.put("u1/p1/abc.pdf", payload, content_type="application/pdf")
    assert local_storage.get("u1/p1/abc.pdf") == payload


def test_local_put_accepts_file_like(local_storage: LocalDiskStorage) -> None:
    payload = b"binary blob"
    local_storage.put(
        "u1/p1/bin.pdf", BytesIO(payload), content_type="application/pdf"
    )
    assert local_storage.get("u1/p1/bin.pdf") == payload


def test_local_get_missing_raises(local_storage: LocalDiskStorage) -> None:
    with pytest.raises(StorageError):
        local_storage.get("does/not/exist.pdf")


def test_local_path_yields_real_file(
    local_storage: LocalDiskStorage, local_root: Path
) -> None:
    payload = b"%PDF-1.4 fake content"
    local_storage.put("u/p/x.pdf", payload, content_type="application/pdf")

    with local_storage.local_path("u/p/x.pdf") as path:
        assert path.is_file()
        assert path.read_bytes() == payload
        # LocalDisk yields the live storage path so it sits under root.
        assert local_root in path.parents


def test_local_path_missing_raises(local_storage: LocalDiskStorage) -> None:
    with pytest.raises(StorageError):
        with local_storage.local_path("missing.pdf"):
            pass  # pragma: no cover


def test_local_delete_removes(local_storage: LocalDiskStorage) -> None:
    local_storage.put("u/p/del.pdf", b"x", content_type="application/pdf")
    local_storage.delete("u/p/del.pdf")
    with pytest.raises(StorageError):
        local_storage.get("u/p/del.pdf")


def test_local_delete_missing_is_silent(local_storage: LocalDiskStorage) -> None:
    # delete() is intentionally idempotent (mirrors S3 semantics).
    local_storage.delete("never/existed.pdf")


def test_local_signed_url_shape(local_storage: LocalDiskStorage) -> None:
    url = local_storage.signed_url("u/p/x.pdf")
    assert url == "/storage/u/p/x.pdf"


def test_local_rejects_unsafe_keys(local_storage: LocalDiskStorage) -> None:
    for bad in ("/abs/path.pdf", "../escape.pdf", "u/../escape.pdf"):
        with pytest.raises(StorageError):
            local_storage.put(bad, b"x", content_type="application/pdf")


def test_local_satisfies_storage_protocol(
    local_storage: LocalDiskStorage,
) -> None:
    assert isinstance(local_storage, Storage)


# ── Factory ─────────────────────────────────────────────────────────


def test_factory_returns_local_when_configured(
    monkeypatch: pytest.MonkeyPatch, local_root: Path
) -> None:
    reset_storage()
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(local_root))
    # Reset the lru_cache on get_settings so it picks up the env override.
    from backend import config as cfg

    cfg.get_settings.cache_clear()

    storage = get_storage()
    try:
        assert isinstance(storage, LocalDiskStorage)
        # Round-trip works through the singleton too.
        storage.put("smoke/key.pdf", b"y", content_type="application/pdf")
        assert storage.get("smoke/key.pdf") == b"y"
    finally:
        reset_storage()
        cfg.get_settings.cache_clear()


def test_factory_caches_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_storage()
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    from backend import config as cfg

    cfg.get_settings.cache_clear()

    a = get_storage()
    b = get_storage()
    try:
        assert a is b
    finally:
        reset_storage()
        cfg.get_settings.cache_clear()


# ── SupabaseStorage smoke (skipped unless live creds present) ───────


_SUPABASE_LIVE = bool(
    os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)


@pytest.mark.skipif(not _SUPABASE_LIVE, reason="Supabase creds not set")
def test_supabase_storage_constructs() -> None:
    """Live smoke — only verifies the client builds + bucket handle works."""
    storage = SupabaseStorage()
    # bucket handle pulled lazily; failing here is a misconfigured project.
    assert storage.bucket is not None
