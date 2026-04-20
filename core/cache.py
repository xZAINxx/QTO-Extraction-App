"""SQLite result cache keyed by sha256(filename + filesize)."""
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Optional

from core.qto_row import QTORow


def _fingerprint(pdf_path: str) -> str:
    p = Path(pdf_path)
    raw = f"{p.name}:{p.stat().st_size}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _row_to_dict(r: QTORow) -> dict:
    return {
        "s_no": r.s_no, "tag": r.tag, "drawings_details": r.drawings_details,
        "description": r.description, "qty": r.qty, "units": r.units,
        "unit_price": r.unit_price, "total_formula": r.total_formula,
        "trade_division": r.trade_division, "is_header_row": r.is_header_row,
        "source_page": r.source_page, "source_sheet": r.source_sheet,
        "extraction_method": r.extraction_method, "confidence": r.confidence,
        "needs_review": r.needs_review,
    }


def _dict_to_row(d: dict) -> QTORow:
    return QTORow(**d)


class ResultCache:
    def __init__(self, cache_dir: str = "./cache"):
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        self._db = Path(cache_dir) / "qto_cache.db"
        self._conn = sqlite3.connect(str(self._db))
        self._init_schema()

    def _init_schema(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS extractions (
                fingerprint TEXT PRIMARY KEY,
                pdf_name TEXT,
                rows_json TEXT,
                page_classifications TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.commit()

    def fingerprint(self, pdf_path: str) -> str:
        return _fingerprint(pdf_path)

    def load(self, pdf_path: str) -> Optional[list[QTORow]]:
        fp = _fingerprint(pdf_path)
        cur = self._conn.execute(
            "SELECT rows_json FROM extractions WHERE fingerprint=?", (fp,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return [_dict_to_row(d) for d in json.loads(row[0])]

    def save(self, pdf_path: str, rows: list[QTORow], classifications: dict | None = None):
        fp = _fingerprint(pdf_path)
        name = Path(pdf_path).name
        rows_json = json.dumps([_row_to_dict(r) for r in rows])
        cls_json = json.dumps(classifications or {})
        self._conn.execute(
            """INSERT OR REPLACE INTO extractions
               (fingerprint, pdf_name, rows_json, page_classifications)
               VALUES (?, ?, ?, ?)""",
            (fp, name, rows_json, cls_json),
        )
        self._conn.commit()

    def load_classifications(self, pdf_path: str) -> Optional[dict]:
        fp = _fingerprint(pdf_path)
        cur = self._conn.execute(
            "SELECT page_classifications FROM extractions WHERE fingerprint=?", (fp,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def clear(self, pdf_path: str):
        fp = _fingerprint(pdf_path)
        self._conn.execute("DELETE FROM extractions WHERE fingerprint=?", (fp,))
        self._conn.commit()

    def close(self):
        self._conn.close()
