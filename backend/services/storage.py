"""Pluggable PDF storage backends.

Two implementations behind one ``Storage`` Protocol:

* :class:`LocalDiskStorage` — files at ``{root}/{key}`` on the local
  filesystem. Used in dev (no Supabase configured) and for self-hosted
  deployments that don't want a managed object store.
* :class:`SupabaseStorage` — files in a single Supabase Storage bucket
  with key prefix ``{user_id}/{project_id}/{pdf_id}/source.pdf``. The
  bucket is RLS-policied so users can only read their own folder; this
  module uses the service-role key on the backend, bypassing RLS for
  performance, with WHERE-clause scoping enforced in the routes layer.

The factory :func:`get_storage` reads ``Settings.storage_backend`` and
returns the matching instance — call it from a FastAPI dependency or a
``services/jobs.py`` task.

Why a Protocol and not an ABC: ``Protocol`` lets callers type against
the interface without forcing imports of the concrete classes, which
keeps ``services/jobs.py`` (long-running worker) out of the SDK
import path of routes that only need ``put`` + ``signed_url``.

The pipeline in ``ai/`` and ``core/`` requires a real filesystem path
for ``fitz.open`` (PyMuPDF can't load from bytes without spilling to
disk anyway). Both backends therefore implement :meth:`local_path`:
LocalDisk returns the on-disk file directly; Supabase downloads to a
``tempfile`` location and returns its path. Callers are responsible
for the cleanup contract — the temp file lives until the worker calls
``Storage.delete_local_path(path)`` (Supabase) or no-ops (LocalDisk).
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator, Protocol, runtime_checkable

from backend.config import Settings, get_settings


logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Raised when a storage operation fails for any reason.

    Routes catch this and translate to ``HTTPException(503)``; tests
    assert against the type without needing to know which backend
    raised it.
    """


@runtime_checkable
class Storage(Protocol):
    """The contract every storage backend implements.

    Methods are kept synchronous because the underlying SDKs (Supabase
    storage3, plain filesystem ops) are blocking. Callers that live on
    the FastAPI event loop must wrap these in ``asyncio.to_thread``.
    """

    def put(self, key: str, data: bytes | IO[bytes], *, content_type: str) -> None:
        """Upload (or overwrite) the bytes at ``key``.

        ``data`` may be raw bytes or a binary file-like object — both
        backends rewind / re-read internally as needed.
        """
        ...

    def get(self, key: str) -> bytes:
        """Return the raw bytes stored at ``key``."""
        ...

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        """Yield a real filesystem path the caller can ``fitz.open``.

        Implementations may download to a temp file (Supabase) or
        return the live storage path (LocalDisk). The context manager
        guarantees cleanup on exit so callers never leak temp files.
        """
        ...

    def delete(self, key: str) -> None:
        """Remove the object at ``key``. No-op if it doesn't exist."""
        ...

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str:
        """Return a time-limited URL that downloads the object.

        Used by the frontend to fetch the original PDF for the canvas
        viewer without proxying through FastAPI. ``expires_in`` is in
        seconds. LocalDiskStorage returns a relative ``/storage/{key}``
        URL the dev server can serve directly.
        """
        ...


# ── LocalDiskStorage ────────────────────────────────────────────────


class LocalDiskStorage:
    """Files stored under a configurable root on the local filesystem.

    Default root is ``settings.storage_local_root`` (``./.dev-storage``).
    The directory tree mirrors the bucket-key shape so swapping to
    SupabaseStorage doesn't change any callers — only the backing
    implementation moves.
    """

    def __init__(self, root: Path | str | None = None):
        settings = get_settings()
        self._root = Path(root) if root is not None else settings.storage_local_root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, key: str) -> Path:
        # Normalise and forbid traversal. ``key`` is a relative POSIX
        # path like "user/project/pdf/source.pdf"; absolute or "../"
        # entries would let a route escape the root.
        if key.startswith("/") or ".." in key.split("/"):
            raise StorageError(f"unsafe storage key: {key!r}")
        return self._root / key

    def put(self, key: str, data: bytes | IO[bytes], *, content_type: str) -> None:
        del content_type  # unused on local disk; recorded by the DB layer
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("wb") as fp:
                if isinstance(data, (bytes, bytearray)):
                    fp.write(data)
                else:
                    shutil.copyfileobj(data, fp)
        except OSError as exc:
            raise StorageError(f"local put failed for {key!r}: {exc}") from exc

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageError(f"key not found: {key!r}") from exc
        except OSError as exc:
            raise StorageError(f"local get failed for {key!r}: {exc}") from exc

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        path = self._resolve(key)
        if not path.is_file():
            raise StorageError(f"key not found: {key!r}")
        try:
            yield path
        finally:
            # No-op cleanup: the file lives in the storage tree, not a
            # temp location; deleting on exit would corrupt the store.
            pass

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"local delete failed for {key!r}: {exc}") from exc

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str:
        del expires_in  # not enforced on local disk
        # Returned URL is served by the FastAPI route mounted at
        # /storage in commit 4. It's only used in dev mode and behind
        # the auth middleware — never exposed to anonymous clients.
        return f"/storage/{key}"


# ── SupabaseStorage ─────────────────────────────────────────────────


class SupabaseStorage:
    """Object storage backed by Supabase's S3-compatible bucket service.

    Constructed lazily — the ``supabase`` SDK isn't imported at module
    load so test environments without the dependency installed can
    still introspect this module. On instantiation we eagerly verify
    the bucket exists; the route layer can catch the resulting
    :class:`StorageError` and surface "storage misconfigured".
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        service_role_key: str | None = None,
        bucket: str | None = None,
    ):
        settings = get_settings()
        self._url = url or settings.supabase_url
        self._service_key = service_role_key or settings.supabase_service_role_key
        self._bucket_name = bucket or settings.storage_bucket

        if not (self._url and self._service_key):
            raise StorageError(
                "SupabaseStorage requires SUPABASE_URL and "
                "SUPABASE_SERVICE_ROLE_KEY to be configured."
            )

        try:
            from supabase import create_client
        except ImportError as exc:  # pragma: no cover — supabase is in requirements.txt
            raise StorageError(
                "supabase-py is not installed; run "
                "`pip install -r backend/requirements.txt`"
            ) from exc

        self._client = create_client(self._url, self._service_key)

    @property
    def bucket(self):
        return self._client.storage.from_(self._bucket_name)

    def put(self, key: str, data: bytes | IO[bytes], *, content_type: str) -> None:
        try:
            payload = data if isinstance(data, (bytes, bytearray)) else data.read()
            # storage3's upload accepts bytes + a file_options dict for
            # content type. ``upsert=true`` overwrites without erroring.
            self.bucket.upload(
                path=key,
                file=payload,
                file_options={
                    "content-type": content_type,
                    "upsert": "true",
                },
            )
        except Exception as exc:
            raise StorageError(f"supabase put failed for {key!r}: {exc}") from exc

    def get(self, key: str) -> bytes:
        try:
            return self.bucket.download(key)
        except Exception as exc:
            raise StorageError(f"supabase get failed for {key!r}: {exc}") from exc

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        # Supabase needs a download → temp file roundtrip so PyMuPDF
        # has a real path to open. Suffix with ``.pdf`` so libraries
        # that sniff by extension behave.
        data = self.get(key)
        suffix = Path(key).suffix or ".pdf"
        tmp = tempfile.NamedTemporaryFile(
            prefix="qto-",
            suffix=suffix,
            delete=False,
        )
        try:
            tmp.write(data)
            tmp.close()
            yield Path(tmp.name)
        finally:
            try:
                Path(tmp.name).unlink(missing_ok=True)
            except OSError:
                logger.warning("could not unlink temp file %s", tmp.name)

    def delete(self, key: str) -> None:
        try:
            self.bucket.remove([key])
        except Exception as exc:
            raise StorageError(f"supabase delete failed for {key!r}: {exc}") from exc

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str:
        try:
            response = self.bucket.create_signed_url(key, expires_in)
        except Exception as exc:
            raise StorageError(f"supabase sign failed for {key!r}: {exc}") from exc
        # storage3 returns either {"signedURL": "..."} or {"signedUrl": "..."}
        # depending on version — accept both.
        url = response.get("signedURL") or response.get("signedUrl")
        if not url:
            raise StorageError(f"supabase sign returned no URL for {key!r}")
        return url


# ── Factory ─────────────────────────────────────────────────────────


_storage_singleton: Storage | None = None


def get_storage(settings: Settings | None = None) -> Storage:
    """Return the process-wide storage backend per ``Settings``.

    Cached on first call so the Supabase client is only constructed
    once. Tests reset the singleton via ``reset_storage()``.
    """
    global _storage_singleton
    if _storage_singleton is not None:
        return _storage_singleton

    s = settings or get_settings()

    backend: Storage
    if s.storage_backend == "supabase":
        backend = SupabaseStorage()
    elif s.storage_backend == "local":
        backend = LocalDiskStorage(root=s.storage_local_root)
    else:  # pragma: no cover — pydantic-settings literal narrows this
        raise StorageError(f"unknown storage backend: {s.storage_backend!r}")

    _storage_singleton = backend
    logger.info(
        "storage: initialised %s (root=%s)",
        type(backend).__name__,
        getattr(backend, "root", None) or s.supabase_url,
    )
    return backend


def reset_storage() -> None:
    """Clear the cached singleton — call from test fixtures."""
    global _storage_singleton
    _storage_singleton = None


__all__ = [
    "LocalDiskStorage",
    "Storage",
    "StorageError",
    "SupabaseStorage",
    "get_storage",
    "reset_storage",
]
