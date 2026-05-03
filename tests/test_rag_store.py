"""Tests for ``core.rag_store.HistoricalStore``.

Covers schema bootstrap, insert/round-trip, cosine ranking, project
filtering, used-count increments, and input-type tolerance (list vs
``np.ndarray``). All tests use ``tmp_path`` so each test gets its own
fresh SQLite file.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.rag_store import HistoricalStore


def _store(tmp_path) -> HistoricalStore:
    """Open a fresh store under the test's tmp dir."""
    return HistoricalStore({"store_path": str(tmp_path / "historical.db")})


def test_store_creates_db_file_and_table(tmp_path):
    db_path = tmp_path / "historical.db"
    store = HistoricalStore({"store_path": str(db_path)})
    try:
        assert db_path.exists(), "SQLite file should be created on init"
        assert store.count() == 0
        # Table must be queryable (raises if missing).
        cur = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='historical_descriptions'"
        )
        assert cur.fetchone() is not None
    finally:
        store.close()


def test_store_creates_parent_directory(tmp_path):
    nested = tmp_path / "deep" / "nested" / "dir" / "historical.db"
    store = HistoricalStore({"store_path": str(nested)})
    try:
        assert nested.exists()
        assert nested.parent.is_dir()
    finally:
        store.close()


def test_store_add_returns_row_id_and_increments(tmp_path):
    store = _store(tmp_path)
    try:
        emb = [0.1, 0.2, 0.3]
        first = store.add("raw1", "NORMALIZED 1", emb, sheet="A-101",
                          keynote_ref="1/A101", project_name="Proj-A")
        second = store.add("raw2", "NORMALIZED 2", emb, project_name="Proj-A")
        assert first == 1
        assert second == 2
        assert store.count() == 2
    finally:
        store.close()


def test_store_search_returns_top_k_sorted_by_cosine_desc(tmp_path):
    store = _store(tmp_path)
    try:
        # Three orthogonal-ish embeddings; query is closest to the second.
        store.add("raw_a", "NORM A", [1.0, 0.0, 0.0])
        target_id = store.add("raw_b", "NORM B", [0.0, 1.0, 0.0])
        store.add("raw_c", "NORM C", [0.0, 0.0, 1.0])

        results = store.search([0.0, 0.95, 0.05], top_k=3)
        assert len(results) == 3
        # Sorted descending by score.
        scores = [s for s, _ in results]
        assert scores == sorted(scores, reverse=True)
        # Top hit must be the embedding closest to the query (raw_b).
        top_score, top_row = results[0]
        assert top_row["id"] == target_id
        assert top_row["normalized"] == "NORM B"
        assert top_score > 0.95
    finally:
        store.close()


def test_store_search_top_k_truncates(tmp_path):
    store = _store(tmp_path)
    try:
        for i in range(5):
            store.add(f"raw_{i}", f"NORM {i}", [float(i + 1), 0.0, 0.0])
        results = store.search([1.0, 0.0, 0.0], top_k=2)
        assert len(results) == 2
    finally:
        store.close()


def test_store_search_filters_by_project(tmp_path):
    store = _store(tmp_path)
    try:
        store.add("a1", "NORM A1", [1.0, 0.0], project_name="ProjA")
        store.add("a2", "NORM A2", [0.9, 0.1], project_name="ProjA")
        store.add("b1", "NORM B1", [1.0, 0.0], project_name="ProjB")
        store.add("b2", "NORM B2", [0.5, 0.5], project_name="ProjB")

        results = store.search([1.0, 0.0], top_k=10, project="ProjA")
        assert len(results) == 2
        assert all(row["project_name"] == "ProjA" for _, row in results)

        results_b = store.search([1.0, 0.0], top_k=10, project="ProjB")
        assert len(results_b) == 2
        assert all(row["project_name"] == "ProjB" for _, row in results_b)
    finally:
        store.close()


def test_store_search_empty_returns_empty_list(tmp_path):
    store = _store(tmp_path)
    try:
        assert store.search([1.0, 0.0, 0.0], top_k=5) == []
        assert store.search([1.0, 0.0, 0.0], top_k=5, project="Missing") == []
    finally:
        store.close()


def test_store_increment_used_count(tmp_path):
    store = _store(tmp_path)
    try:
        row_id = store.add("raw", "NORM", [0.1, 0.2])
        results = store.search([0.1, 0.2], top_k=1)
        assert results[0][1]["used_count"] == 0

        store.increment_used_count(row_id)
        store.increment_used_count(row_id)
        results = store.search([0.1, 0.2], top_k=1)
        assert results[0][1]["used_count"] == 2
    finally:
        store.close()


def test_store_handles_numpy_array_input(tmp_path):
    store = _store(tmp_path)
    try:
        emb_np = np.array([0.5, 0.5, 0.5], dtype=np.float64)
        row_id = store.add("raw", "NORM", emb_np)
        # Search with a numpy query too — round-trip must be lossless to f32.
        results = store.search(np.array([0.5, 0.5, 0.5]), top_k=1)
        assert results[0][1]["id"] == row_id
        assert results[0][0] == pytest.approx(1.0, abs=1e-5)
    finally:
        store.close()


def test_store_handles_list_input(tmp_path):
    store = _store(tmp_path)
    try:
        row_id = store.add("raw", "NORM", [0.5, 0.5, 0.5])
        results = store.search([0.5, 0.5, 0.5], top_k=1)
        assert results[0][1]["id"] == row_id
        assert results[0][0] == pytest.approx(1.0, abs=1e-5)
    finally:
        store.close()


def test_store_count_with_and_without_project(tmp_path):
    store = _store(tmp_path)
    try:
        store.add("a", "A", [1.0], project_name="ProjA")
        store.add("b", "B", [1.0], project_name="ProjA")
        store.add("c", "C", [1.0], project_name="ProjB")
        assert store.count() == 3
        assert store.count(project="ProjA") == 2
        assert store.count(project="ProjB") == 1
        assert store.count(project="Nonexistent") == 0
    finally:
        store.close()
