"""SQLite-backed historical line-item store for RAG priming.

Holds previously-normalized QTO descriptions paired with their raw inputs and
embeddings, plus optional sheet / keynote / project metadata. Search performs
in-Python cosine similarity over a numpy matrix — fast enough for tens of
thousands of rows and avoids the ``sqlite-vec`` install pain on macOS.

Embeddings are stored as ``np.float32`` BLOBs. Inputs may be a
``list[float]`` or any ``np.ndarray``-coercible object; both are normalized
to ``np.float32`` on the way in and on the way out.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Union

import numpy as np

# Type alias: callers may pass plain Python lists or numpy arrays.
EmbeddingLike = Union[list[float], np.ndarray]


def _to_float32(embedding: EmbeddingLike) -> np.ndarray:
    """Coerce any embedding-shaped input to a contiguous ``float32`` 1-D array."""
    arr = np.asarray(embedding, dtype=np.float32)
    if arr.ndim != 1:
        arr = arr.reshape(-1)
    return np.ascontiguousarray(arr)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D float arrays. Safe for zero vectors."""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


class HistoricalStore:
    """SQLite + numpy historical-description store with cosine search.

    The store is intended to be opened once per process and reused. SQLite's
    default single-thread access pattern is sufficient — the QTO tool runs the
    extractor on one worker thread.
    """

    def __init__(self, config: dict):
        """Open (and create if needed) the historical-store SQLite database.

        Args:
            config: Dict with optional key ``store_path`` (default
                ``"./cache/historical.db"``). The parent directory is created
                if it does not exist.
        """
        store_path = config.get("store_path", "./cache/historical.db")
        path = Path(store_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = path
        self._conn = sqlite3.connect(str(path))
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_descriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_input TEXT NOT NULL,
                normalized TEXT NOT NULL,
                sheet TEXT,
                keynote_ref TEXT,
                project_name TEXT,
                embedding BLOB NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                used_count INTEGER DEFAULT 0
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hist_proj "
            "ON historical_descriptions(project_name)"
        )
        self._conn.commit()

    def add(
        self,
        raw: str,
        normalized: str,
        embedding: EmbeddingLike,
        sheet: str = "",
        keynote_ref: str = "",
        project_name: str = "",
    ) -> int:
        """Insert one historical entry. Returns the new row id."""
        blob = _to_float32(embedding).tobytes()
        cur = self._conn.execute(
            """
            INSERT INTO historical_descriptions
                (raw_input, normalized, sheet, keynote_ref, project_name, embedding)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (raw, normalized, sheet, keynote_ref, project_name, blob),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def search(
        self,
        query_embedding: EmbeddingLike,
        top_k: int = 20,
        project: Optional[str] = None,
    ) -> list[tuple[float, dict]]:
        """Return the ``top_k`` rows most similar to ``query_embedding``.

        Performs an in-Python cosine-similarity scan over the (optionally
        project-filtered) row set. Returns a list of ``(score, row_dict)``
        tuples sorted by score descending. Each row dict contains
        ``id, raw_input, normalized, sheet, keynote_ref, project_name,
        used_count``. Empty result set yields an empty list.
        """
        query = _to_float32(query_embedding)
        if project is not None:
            cur = self._conn.execute(
                """
                SELECT id, raw_input, normalized, sheet, keynote_ref,
                       project_name, used_count, embedding
                FROM historical_descriptions
                WHERE project_name = ?
                """,
                (project,),
            )
        else:
            cur = self._conn.execute(
                """
                SELECT id, raw_input, normalized, sheet, keynote_ref,
                       project_name, used_count, embedding
                FROM historical_descriptions
                """
            )
        scored: list[tuple[float, dict]] = []
        for row in cur.fetchall():
            row_id, raw, normalized, sheet, keynote_ref, proj, used, blob = row
            emb = np.frombuffer(blob, dtype=np.float32)
            score = _cosine(query, emb)
            scored.append(
                (
                    score,
                    {
                        "id": int(row_id),
                        "raw_input": raw,
                        "normalized": normalized,
                        "sheet": sheet or "",
                        "keynote_ref": keynote_ref or "",
                        "project_name": proj or "",
                        "used_count": int(used),
                    },
                )
            )
        scored.sort(key=lambda t: t[0], reverse=True)
        return scored[:top_k]

    def increment_used_count(self, row_id: int) -> None:
        """Bump ``used_count`` for the given row by one. No-op if id missing."""
        self._conn.execute(
            "UPDATE historical_descriptions SET used_count = used_count + 1 "
            "WHERE id = ?",
            (row_id,),
        )
        self._conn.commit()

    def count(self, project: Optional[str] = None) -> int:
        """Return the number of stored rows, optionally filtered by project."""
        if project is not None:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM historical_descriptions WHERE project_name = ?",
                (project,),
            )
        else:
            cur = self._conn.execute("SELECT COUNT(*) FROM historical_descriptions")
        return int(cur.fetchone()[0])

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
