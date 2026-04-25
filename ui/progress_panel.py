"""Per-page progress panel with status icons and retry capability."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QPushButton, QFrame, QProgressBar,
)
from PyQt6.QtCore import Qt, pyqtSignal

from ui.theme import SURFACE_2, TEXT_1, TEXT_2, TEXT_3, BORDER_HEX, AMBER, RED, EMERALD, INDIGO


_ICONS = {
    "pending":  "○",
    "running":  "◉",
    "done":     "✓",
    "skipped":  "⊘",
    "failed":   "✗",
}

_COLORS = {
    "pending":  TEXT_3,
    "running":  INDIGO,
    "done":     EMERALD,
    "skipped":  TEXT_3,
    "failed":   RED,
}


class PageRow(QFrame):
    retry_requested = pyqtSignal(int)

    def __init__(self, page_num: int, parent=None):
        super().__init__(parent)
        self._page = page_num
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)

        self._icon = QLabel("○")
        self._icon.setFixedWidth(16)
        self._label = QLabel(f"Page {page_num}")
        self._label.setStyleSheet(f"color: {TEXT_3}; font-size: 11px;")
        self._type = QLabel("")
        self._type.setStyleSheet(f"color: {TEXT_2}; font-size: 10px;")
        self._retry_btn = QPushButton("Retry")
        self._retry_btn.setObjectName("retryBtn")
        self._retry_btn.setFixedSize(46, 20)
        self._retry_btn.hide()
        self._retry_btn.clicked.connect(lambda: self.retry_requested.emit(self._page))

        layout.addWidget(self._icon)
        layout.addWidget(self._label)
        layout.addWidget(self._type)
        layout.addStretch()
        layout.addWidget(self._retry_btn)

    def set_status(self, status: str, page_type: str = ""):
        icon = _ICONS.get(status, "○")
        color = _COLORS.get(status, TEXT_3)
        self._icon.setText(icon)
        self._icon.setStyleSheet(f"color: {color}; font-size: 13px;")
        self._label.setStyleSheet(f"color: {color}; font-size: 11px;")
        if page_type:
            self._type.setText(page_type)
        if status == "failed":
            self._retry_btn.show()
        else:
            self._retry_btn.hide()


class ProgressPanel(QWidget):
    retry_page = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        lbl = QLabel("PROCESSING")
        lbl.setObjectName("sectionLabel")
        header.addWidget(lbl)
        header.addStretch()
        layout.addLayout(header)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumHeight(6)
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(f"color: {TEXT_2}; font-size: 11px;")
        layout.addWidget(self._status_label)

        # Phase 7 — batch status row. Hidden until cost-saver kicks in.
        self._batch_label = QLabel("")
        self._batch_label.setStyleSheet(
            f"color: {AMBER}; font-size: 11px; padding: 2px 0px;"
        )
        self._batch_label.setVisible(False)
        layout.addWidget(self._batch_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(200)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._rows_layout = QVBoxLayout(self._content)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch()
        scroll.setWidget(self._content)
        layout.addWidget(scroll)

        self._page_rows: dict[int, PageRow] = {}

    def init_pages(self, total: int):
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(0)
        # Clear existing
        for w in self._page_rows.values():
            self._rows_layout.removeWidget(w)
            w.deleteLater()
        self._page_rows.clear()

    def _get_or_create_page_row(self, page_num: int) -> "PageRow":
        if page_num not in self._page_rows:
            row = PageRow(page_num, self._content)
            row.retry_requested.connect(self.retry_page)
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._page_rows[page_num] = row
        return self._page_rows[page_num]

    def set_page_status(self, page_num: int, status: str, page_type: str = ""):
        self._get_or_create_page_row(page_num).set_status(status, page_type)
        self._progress_bar.setValue(page_num)
        self._status_label.setText(f"Processing page {page_num}/{self._progress_bar.maximum()}")

    def set_page_running(self, page_num: int):
        self._get_or_create_page_row(page_num).set_status("running")

    def set_complete(self):
        self._status_label.setText("Extraction complete")
        self._progress_bar.setValue(self._progress_bar.maximum())

    def set_batch_status(
        self,
        message: str = "",
        *,
        done: bool = False,
        error: bool = False,
    ) -> None:
        """Phase 7 — surface batched-compose progress (cost-saver mode).

        Pass an empty ``message`` to hide the row entirely. ``done`` swaps
        the colour to emerald, ``error`` to red. Anything else stays amber.
        """
        if not message:
            self._batch_label.clear()
            self._batch_label.setVisible(False)
            return
        if error:
            colour = RED
        elif done:
            colour = EMERALD
        else:
            colour = AMBER
        self._batch_label.setStyleSheet(
            f"color: {colour}; font-size: 11px; padding: 2px 0px;"
        )
        self._batch_label.setText(message)
        self._batch_label.setVisible(True)
