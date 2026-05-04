"""SheetRail — left-panel sheet navigator with thumbnails and scope status.

Lists every page in the loaded PDF as a vertical thumbnail with sheet
metadata, discipline pill, and a scope-status dot. Search + discipline
filter chips sit at the top; collapsing the rail to 64 px hides chrome
and shrinks the thumbnails.

Performance:
    * Single global ``QPixmapCache`` (256 MB) shared with other rails.
    * Thumbnails render on ``QThreadPool.globalInstance()`` at low DPI.
      See ``_thumbnail_worker.py`` for the worker + signal proxy.

Persistence:
    * Scope status (``in`` | ``out`` | ``deferred`` | ``done``) is keyed
      by ``f"{filename}:{filesize}"`` and stored as JSON at
      ``{cache_dir}/scope.json`` — see ``_scope_store.py``.

Composition:
    * Per-row widget + scope-color helpers live in ``_sheet_row.py``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

import fitz  # type: ignore[import-untyped]

from PyQt6.QtCore import QSize, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QPixmap, QPixmapCache
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.components import Pill
from ui.panels._scope_store import ScopeStore, fingerprint as _fingerprint
from ui.panels._sheet_row import (
    ScopeStatus,
    SheetMeta as _SheetMeta,
    SheetRow as _SheetRow,
    THUMB_EXPANDED,
)
from ui.panels._thumbnail_worker import _ThumbnailWorker
from ui.theme import tokens

_LOG = logging.getLogger(__name__)

_SCOPE_VALUES: tuple[ScopeStatus, ...] = ("in", "out", "deferred", "done")
_DISCIPLINE_LETTERS: tuple[str, ...] = ("A", "S", "M", "E", "P", "C", "L")
_DISCIPLINE_PLACEHOLDER = "?"

_RAIL_WIDTH_EXPANDED = 220
_RAIL_WIDTH_COLLAPSED = 64


# ---------------------------------------------------------------------------
# Pure-logic helpers (testable without instantiating widgets).
# ---------------------------------------------------------------------------


def _discipline_from_sheet_number(sheet_number: str) -> str:
    """Pull the discipline letter from a normalized sheet number.

    ``"A-101"`` → ``"A"``, ``"S5.1"`` → ``"S"``. Anything not starting
    with a canonical discipline letter resolves to ``"?"``.
    """
    if not sheet_number:
        return _DISCIPLINE_PLACEHOLDER
    head = sheet_number.strip()
    if not head:
        return _DISCIPLINE_PLACEHOLDER
    first = head[0].upper()
    return first if first in _DISCIPLINE_LETTERS else _DISCIPLINE_PLACEHOLDER


# ---------------------------------------------------------------------------
# SheetRail — the public widget.
# ---------------------------------------------------------------------------


class SheetRail(QWidget):
    """Vertical sheet navigator with thumbnails, search, scope status."""

    sheet_clicked = pyqtSignal(int)            # 1-based page number
    scope_changed = pyqtSignal(int, str)       # (page_num, scope status)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        cache_dir: str | Path = "./cache",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sheetRail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(_RAIL_WIDTH_EXPANDED)

        # Global cache cap — 256 MB. Limit is in KB per Qt docs.
        QPixmapCache.setCacheLimit(256 * 1024)

        self._collapsed = False
        self._pdf_path: Optional[str] = None
        self._rows: list[_SheetRow] = []
        self._active_page: int | None = None
        self._search_query: str = ""
        self._discipline_filter: Optional[str] = None
        self._scope_store = ScopeStore(cache_dir=Path(cache_dir))

        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._top = QFrame(self)
        self._top.setObjectName("sheetRailTop")
        top_layout = QVBoxLayout(self._top)
        top_layout.setContentsMargins(
            tokens["space"][3], tokens["space"][3],
            tokens["space"][3], tokens["space"][2],
        )
        top_layout.setSpacing(tokens["space"][2])

        self._search = QLineEdit(self._top)
        self._search.setObjectName("sheetRailSearch")
        self._search.setPlaceholderText("Search sheets…")
        self._search.textChanged.connect(self.search)
        top_layout.addWidget(self._search)

        chips_row = QHBoxLayout()
        chips_row.setContentsMargins(0, 0, 0, 0)
        chips_row.setSpacing(tokens["space"][1])
        self._chips: dict[str, Pill] = {}
        for letter in _DISCIPLINE_LETTERS:
            chip = Pill(letter, variant="neutral", parent=self._top)
            chip.setProperty("toggleable", True)
            chip.mousePressEvent = (  # type: ignore[assignment]
                lambda _evt, l=letter: self._on_chip_clicked(l)
            )
            chips_row.addWidget(chip)
            self._chips[letter] = chip
        chips_row.addStretch()
        top_layout.addLayout(chips_row)
        outer.addWidget(self._top)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("sheetRailScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self._content = QWidget(self._scroll)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(
            tokens["space"][2], tokens["space"][2],
            tokens["space"][2], tokens["space"][2],
        )
        self._content_layout.setSpacing(tokens["space"][2])
        self._content_layout.addStretch()
        self._scroll.setWidget(self._content)
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        outer.addWidget(self._scroll, 1)

    # --- Public API ----------------------------------------------------------

    def load_pdf(self, pdf_path: str) -> None:
        """Open the PDF, populate metadata rows, kick off thumbnail rendering."""
        self.clear()
        self._pdf_path = pdf_path
        try:
            self._scope_store.load(_fingerprint(pdf_path))
        except (OSError, FileNotFoundError):
            self._scope_store.fingerprint = pdf_path
            self._scope_store.data = {}

        try:
            doc = fitz.open(pdf_path)
            count = doc.page_count
            doc.close()
        except Exception as exc:
            _LOG.warning("could not open %s: %s", pdf_path, exc)
            return

        metas = [_SheetMeta(page_num=n) for n in range(1, count + 1)]
        self._populate_from_metadata(metas)
        self._spawn_thumbnail_workers()

    def _populate_from_metadata(self, metas: Iterable[_SheetMeta]) -> None:
        """Test-friendly seam — build rows from metadata without touching fitz."""
        for meta in metas:
            if not meta.discipline:
                meta.discipline = _discipline_from_sheet_number(meta.sheet_number)
            row = _SheetRow(meta, parent=self._content)
            row.clicked.connect(self.sheet_clicked.emit)
            row.scope_changed.connect(self._on_row_scope_changed)
            persisted = self._scope_store.data.get(str(meta.page_num))
            if persisted in _SCOPE_VALUES:
                row.set_scope(persisted, emit=False)  # type: ignore[arg-type]
            self._content_layout.insertWidget(
                self._content_layout.count() - 1, row,
            )
            self._rows.append(row)
        self._apply_filters()

    def clear(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        self._active_page = None

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.setFixedWidth(
            _RAIL_WIDTH_COLLAPSED if collapsed else _RAIL_WIDTH_EXPANDED
        )
        self._top.setVisible(not collapsed)
        for row in self._rows:
            row.set_collapsed(collapsed)

    def set_active_sheet(self, page_num: int) -> None:
        self._active_page = page_num
        for row in self._rows:
            row.set_active(row.meta.page_num == page_num)

    def filter_by_discipline(self, discipline: Optional[str]) -> None:
        self._discipline_filter = discipline
        for letter, chip in self._chips.items():
            chip.setVariant("success" if discipline == letter else "neutral")
        self._apply_filters()

    def search(self, query: str) -> None:
        self._search_query = query
        self._apply_filters()

    def sizeHint(self) -> QSize:  # noqa: D401
        # Pin the hint to match the fixed width so layout siblings can size
        # themselves predictably and the construction tests can read it back.
        width = _RAIL_WIDTH_COLLAPSED if self._collapsed else _RAIL_WIDTH_EXPANDED
        return QSize(width, max(super().sizeHint().height(), 480))

    # --- Internal helpers ----------------------------------------------------

    def _apply_filters(self) -> None:
        for row in self._rows:
            row.setVisible(row.matches(self._search_query, self._discipline_filter))

    def _on_chip_clicked(self, letter: str) -> None:
        new = None if self._discipline_filter == letter else letter
        self.filter_by_discipline(new)

    def _on_row_scope_changed(self, page_num: int, status: str) -> None:
        self._scope_store.set(page_num, status)
        self.scope_changed.emit(page_num, status)

    def _spawn_thumbnail_workers(self) -> None:
        if not self._pdf_path or not self._rows:
            return
        pool = QThreadPool.globalInstance()
        for row in self._rows:
            key = (
                f"sheet_rail::{self._pdf_path}::"
                f"{row.meta.page_num}::{THUMB_EXPANDED.width()}"
            )
            worker = _ThumbnailWorker(
                self._pdf_path, row.meta.page_num, THUMB_EXPANDED, key,
            )
            worker.signals.rendered.connect(self._on_thumbnail_rendered)
            pool.start(worker)

    def _on_thumbnail_rendered(self, page_num: int, pix: QPixmap) -> None:
        for row in self._rows:
            if row.meta.page_num == page_num:
                row.set_thumbnail(pix)
                return

    def _on_scroll_changed(self, _value: int) -> None:
        # Hook for a future viewport-aware re-prioritization scheduler.
        pass


__all__ = [
    "SheetRail",
    "ScopeStatus",
    "_SheetMeta",
    "_SheetRow",
    "_discipline_from_sheet_number",
    "_fingerprint",
]
