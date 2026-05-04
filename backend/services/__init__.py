"""Service layer — orchestrates domain logic for FastAPI routes.

Modules in this package are deliberately route-agnostic so they can be
unit-tested without spinning up the FastAPI app. Routes import the
public surface; tests construct the same classes directly with stub
configs.

Public modules:
    storage   — Storage Protocol + LocalDiskStorage + SupabaseStorage
    (more land in commits 4–11)
"""
from __future__ import annotations

from .storage import (
    LocalDiskStorage,
    Storage,
    StorageError,
    SupabaseStorage,
    get_storage,
)

__all__ = [
    "LocalDiskStorage",
    "Storage",
    "StorageError",
    "SupabaseStorage",
    "get_storage",
]
