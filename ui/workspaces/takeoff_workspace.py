"""TakeoffWorkspace — PDF viewer (left) + DataTable (right).

Wave 2 commit 5 of the dapper-pebble plan. Composes the new
:class:`QtoDataTable` with the legacy ``PDFViewer`` widget inside a
horizontal splitter. Splitter state persists to
``~/.qto_tool/takeoff_splitter.bin`` so the user's preferred PDF /
table balance survives restarts.

PDFViewer is imported lazily inside :py:meth:`load_pdf` because the
legacy ``ui/pdf_viewer.py`` module imports color constants from the
old ``ui/theme.py`` namespace, which the new ``ui/theme/`` package
does not re-export. Importing it eagerly at module load would crash
the whole takeoff package — and tests that don't touch a PDF would
fail before they ran. The lazy hook keeps the workspace constructable
in headless test environments without the PDF dependency.

The trace-back binding (row click ↔ PDF region highlight) is NOT wired
in this commit — that lives in commit 6's TraceLink controller.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from core.qto_row import QTORow
from ui.components.data_table import QtoDataTable

# Persistent splitter state — small binary blob written via QSplitter.saveState.
_SPLITTER_STATE_PATH = Path.home() / ".qto_tool" / "takeoff_splitter.bin"


class TakeoffWorkspace(QWidget):
    """Splitter that hosts the PDF viewer (left) and DataTable (right)."""

    # Forwarded from the inner DataTable so the main window can wire signals
    # without reaching through to the child widget.
    row_jump_requested = pyqtSignal(int, str)
    save_as_assembly_requested = pyqtSignal(int)
    rows_confirmed = pyqtSignal(list)
    review_requested = pyqtSignal(int)
    reextract_requested = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pdf_viewer: Optional[QWidget] = None  # lazy

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setHandleWidth(1)

        # Placeholder QWidget on the left — swapped for PDFViewer on first
        # load_pdf call. We need *something* in the splitter so its sizes
        # restore correctly when state is replayed.
        self._left_placeholder = QWidget(self)
        self._splitter.addWidget(self._left_placeholder)

        self._data_table = QtoDataTable(self)
        self._splitter.addWidget(self._data_table)

        # Default 60/40 split favoring the PDF — matches the legacy layout.
        self._splitter.setStretchFactor(0, 6)
        self._splitter.setStretchFactor(1, 4)
        self._restore_splitter_state()

        outer.addWidget(self._splitter, 1)

        # Forward DataTable signals upward.
        self._data_table.row_jump_requested.connect(self.row_jump_requested)
        self._data_table.save_as_assembly_requested.connect(
            self.save_as_assembly_requested
        )
        self._data_table.rows_confirmed.connect(self.rows_confirmed)
        self._data_table.review_requested.connect(self.review_requested)
        self._data_table.reextract_requested.connect(self.reextract_requested)

    # ----- public API --------------------------------------------------------

    @property
    def data_table(self) -> QtoDataTable:
        return self._data_table

    @property
    def pdf_viewer(self) -> Optional[QWidget]:
        """Return the PDF viewer widget, or None until the first load_pdf."""
        return self._pdf_viewer

    def replace_rows(self, rows: list[QTORow]) -> None:
        self._data_table.replace_rows(rows)

    def load_pdf(self, path: str) -> bool:
        """Lazy-construct the PDF viewer the first time it's needed.

        Returns True on success, False if the PDF couldn't be opened or
        the legacy PDFViewer module couldn't be imported (e.g. running
        headless without PyMuPDF available).
        """
        viewer = self._ensure_pdf_viewer()
        if viewer is None:
            return False
        # PDFViewer.load returns bool already.
        return bool(viewer.load(path))

    def save_state(self) -> bool:
        """Persist splitter sizes; returns True if the write succeeded."""
        try:
            _SPLITTER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _SPLITTER_STATE_PATH.write_bytes(bytes(self._splitter.saveState()))
            return True
        except OSError:
            return False

    # ----- internals ---------------------------------------------------------

    def _ensure_pdf_viewer(self) -> Optional[QWidget]:
        if self._pdf_viewer is not None:
            return self._pdf_viewer
        try:
            # Imported here because ui/pdf_viewer.py imports legacy color
            # constants from ui/theme that the new theme package doesn't
            # re-export. Test environments and code paths that never need
            # a PDF stay clear of that import error.
            from ui.pdf_viewer import PDFViewer  # type: ignore[import]
        except Exception:
            return None
        viewer = PDFViewer(self)
        # Replace the placeholder in slot 0.
        self._splitter.replaceWidget(0, viewer)
        self._left_placeholder.deleteLater()
        self._left_placeholder = viewer  # keep reference so GC doesn't reap it
        self._pdf_viewer = viewer
        return viewer

    def _restore_splitter_state(self) -> None:
        if not _SPLITTER_STATE_PATH.exists():
            return
        try:
            data = _SPLITTER_STATE_PATH.read_bytes()
        except OSError:
            return
        try:
            self._splitter.restoreState(data)
        except Exception:
            # Corrupt state file — silently fall back to default sizes.
            return


__all__ = ["TakeoffWorkspace"]
