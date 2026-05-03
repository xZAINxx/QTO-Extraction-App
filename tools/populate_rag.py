"""Seed the RAG historical store from a curated CSV.

CSV schema (header row required):
    raw,normalized,sheet,keynote_ref,project_name

Only ``raw`` and ``normalized`` are required; the rest may be empty.

Usage:
    python tools/populate_rag.py path/to/seed.csv
    python tools/populate_rag.py path/to/seed.csv --config config.yaml
    python tools/populate_rag.py path/to/seed.csv --project HBT-2025

Embeddings are produced via the NVIDIA NIM ``nv-embed-v1`` endpoint using
the credentials and base URLs from ``config.yaml``. Set ``NVIDIA_API_KEY``
in the environment before running.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai.providers.nvidia_provider import NvidiaProvider
from core.rag_store import HistoricalStore
from core.token_tracker import TokenTracker


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed the RAG historical store from a CSV.")
    p.add_argument("csv_path", type=Path, help="CSV file with raw,normalized columns")
    p.add_argument("--config", type=Path, default=ROOT / "config.yaml", help="config.yaml path")
    p.add_argument("--project", type=str, default="", help="Override project_name for all rows")
    p.add_argument("--dry-run", action="store_true", help="Parse CSV and embed but don't write")
    return p.parse_args()


def _load_config(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _read_rows(csv_path: Path, project_override: str) -> list[dict]:
    rows: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            raw = (row.get("raw") or "").strip()
            normalized = (row.get("normalized") or "").strip()
            if not raw or not normalized:
                print(f"  skip line {i}: empty raw or normalized", file=sys.stderr)
                continue
            rows.append({
                "raw": raw,
                "normalized": normalized,
                "sheet": (row.get("sheet") or "").strip(),
                "keynote_ref": (row.get("keynote_ref") or "").strip(),
                "project_name": project_override or (row.get("project_name") or "").strip(),
            })
    return rows


def main() -> int:
    args = _parse_args()
    if not args.csv_path.exists():
        print(f"error: CSV not found: {args.csv_path}", file=sys.stderr)
        return 2
    if not os.environ.get("NVIDIA_API_KEY"):
        print("error: NVIDIA_API_KEY not set in environment", file=sys.stderr)
        return 2

    config = _load_config(args.config)
    rag_cfg = config.get("rag", {}) or {}
    embedding_model = rag_cfg.get("embedding_model", "nvidia/nv-embed-v1")

    rows = _read_rows(args.csv_path, args.project)
    print(f"parsed {len(rows)} valid rows from {args.csv_path}")
    if not rows:
        return 0

    tracker = TokenTracker()
    nvidia = NvidiaProvider(config, tracker)

    store = HistoricalStore(rag_cfg) if not args.dry_run else None

    added = 0
    for i, r in enumerate(rows, start=1):
        if args.dry_run:
            print(f"  [{i}] would insert: {r['raw'][:60]}...")
            continue
        try:
            [emb] = nvidia.embed(embedding_model, [r["raw"]])
        except Exception as exc:
            print(f"  embed failure on row {i}: {exc}", file=sys.stderr)
            continue
        row_id = store.add(
            raw=r["raw"],
            normalized=r["normalized"],
            embedding=emb,
            sheet=r["sheet"],
            keynote_ref=r["keynote_ref"],
            project_name=r["project_name"],
        )
        added += 1
        if i % 25 == 0 or i == len(rows):
            print(f"  inserted {added}/{len(rows)} (last id={row_id})")

    if store is not None:
        total = store.count()
        store.close()
        print(f"done — added {added} rows; store now contains {total} entries")
    else:
        print(f"done — dry run, would have added {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
