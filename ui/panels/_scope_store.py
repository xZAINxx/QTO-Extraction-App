"""Scope-status persistence for ``SheetRail``.

JSON-on-disk store keyed by a PDF fingerprint. The schema looks like::

    {
        "<filename>:<filesize>": {
            "<page_num>": "in" | "out" | "deferred" | "done",
            ...
        },
        ...
    }

The fingerprint format matches ``core/cache.py`` minus the sha256 hashing
so the file stays human-readable / hand-editable. Loaded on
``SheetRail.load_pdf`` and re-flushed on every ``scope_changed`` emit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def fingerprint(pdf_path: str | Path) -> str:
    """Return ``"<filename>:<filesize>"`` for a PDF path on disk."""
    p = Path(pdf_path)
    return f"{p.name}:{p.stat().st_size}"


@dataclass
class ScopeStore:
    """Lightweight JSON-backed store for per-sheet scope status."""

    cache_dir: Path
    fingerprint: str = ""
    data: dict[str, str] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.cache_dir / "scope.json"

    def load(self, pdf_fingerprint: str) -> dict[str, str]:
        """Read the JSON file, isolate this PDF's bucket, return it."""
        self.fingerprint = pdf_fingerprint
        if not self.path.exists():
            self.data = {}
            return self.data
        try:
            blob = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            blob = {}
        self.data = dict(blob.get(pdf_fingerprint, {}))
        return self.data

    def set(self, page_num: int, status: str) -> None:
        """Update one page's scope and persist immediately."""
        self.data[str(page_num)] = status
        self._flush()

    def _flush(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            blob = json.loads(self.path.read_text()) if self.path.exists() else {}
        except (OSError, json.JSONDecodeError):
            blob = {}
        blob[self.fingerprint] = dict(self.data)
        self.path.write_text(json.dumps(blob, indent=2))


__all__ = ["ScopeStore", "fingerprint"]
