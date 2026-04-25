"""Filterable editable QTO results table with section header styling."""
import subprocess
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QComboBox, QLineEdit, QLabel, QMenu, QAbstractItemView, QHeaderView,
    QPushButton, QFrame, QStackedWidget,
)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal

from core.qto_row import QTORow
from PyQt6.QtGui import QColor, QBrush, QFont, QAction, QPalette
from ui.theme import (
    SURFACE_1, SURFACE_2, SURFACE_3, SECTION_BG, TEXT_1, TEXT_2, TEXT_3,
    BORDER_HEX, AMBER, INDIGO, CANVAS,
)

_COLS = ["S.NO", "DRAWINGS", "DETAILS", "DESCRIPTION OF WORK", "QTY", "UNITS", "UNIT PRICE", "TOTAL"]
_COL_EDITABLE = {3, 4, 5, 6}   # Description, QTY, Units, Unit Price are editable

_AMBER_COLOR = QColor(AMBER)
_SECTION_BG_COLOR = QColor(SECTION_BG)
_SURFACE_2_COLOR = QColor(SURFACE_2)
_TEXT_1_COLOR = QColor(TEXT_1)
_TEXT_2_COLOR = QColor(TEXT_2)
_TEXT_3_COLOR = QColor(TEXT_3)


class ResultsTable(QWidget):
    row_jump_requested = pyqtSignal(int, str)   # (page_num, source_sheet)
    save_as_assembly_requested = pyqtSignal(int)   # row index in self._rows

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[QTORow] = []
        self._filtered_indices: list[int] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Filter bar
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)

        self._trade_filter = QComboBox()
        self._trade_filter.addItem("All Trades")
        self._trade_filter.setFixedWidth(160)
        self._trade_filter.currentTextChanged.connect(self._apply_filters)

        self._sheet_filter = QComboBox()
        self._sheet_filter.addItem("All Sheets")
        self._sheet_filter.setFixedWidth(120)
        self._sheet_filter.currentTextChanged.connect(self._apply_filters)

        self._keyword_filter = QLineEdit()
        self._keyword_filter.setPlaceholderText("Search description…")
        self._keyword_filter.textChanged.connect(self._apply_filters)

        self._review_filter = QPushButton("⚠ Needs Review")
        self._review_filter.setCheckable(True)
        self._review_filter.setFixedHeight(32)
        self._review_filter.setStyleSheet(
            f"QPushButton {{ background: {SURFACE_2}; color: {AMBER}; border: 1px solid {AMBER}; "
            f"border-radius: 6px; padding: 4px 10px; font-size: 12px; }}"
            f"QPushButton:checked {{ background: {AMBER}; color: {CANVAS}; }}"
        )
        self._review_filter.toggled.connect(self._apply_filters)

        filter_bar.addWidget(QLabel("Trade:"))
        filter_bar.addWidget(self._trade_filter)
        filter_bar.addWidget(QLabel("Sheet:"))
        filter_bar.addWidget(self._sheet_filter)
        filter_bar.addWidget(self._keyword_filter)
        filter_bar.addWidget(self._review_filter)
        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        # Table
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)   # Description stretches
        hh.setDefaultSectionSize(90)
        self._table.setColumnWidth(0, 50)   # S.NO
        self._table.setColumnWidth(1, 140)  # Drawings
        self._table.setColumnWidth(2, 50)   # Tag
        self._table.setColumnWidth(4, 70)   # QTY
        self._table.setColumnWidth(5, 70)   # Units
        self._table.setColumnWidth(6, 90)   # Unit Price
        self._table.setColumnWidth(7, 90)   # Total

        # Empty state
        self._empty_state = self._build_empty_state()

        # Stack: show table or empty state
        self._stack = QStackedWidget()
        self._stack.addWidget(self._empty_state)  # index 0
        self._stack.addWidget(self._table)         # index 1
        self._stack.setCurrentIndex(0)

        layout.addWidget(self._stack)

    def _build_empty_state(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(
            f"QWidget {{ background: {SURFACE_1}; border-radius: 8px; border: 1px solid {BORDER_HEX}; }}"
        )
        inner = QVBoxLayout(container)
        inner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.setSpacing(8)

        icon = QLabel("pdf")
        icon_font = QFont(".AppleSystemUIFont")
        icon_font.setPointSize(10)
        icon_font.setBold(True)
        icon.setFont(icon_font)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"color: {BORDER_HEX}; background: {SURFACE_3}; border: 1px solid {BORDER_HEX};"
            f"border-radius: 6px; padding: 6px 10px; letter-spacing: 0.12em;"
        )

        title = QLabel("No drawing set loaded")
        title_font = QFont(".AppleSystemUIFont")
        title_font.setPointSize(13)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {TEXT_2}; background: transparent; border: none;")

        subtitle = QLabel("Drop a PDF into the sidebar or click Browse to begin")
        subtitle_font = QFont(".AppleSystemUIFont")
        subtitle_font.setPointSize(11)
        subtitle.setFont(subtitle_font)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {TEXT_3}; background: transparent; border: none;")
        subtitle.setWordWrap(True)
        subtitle.setMaximumWidth(320)

        inner.addStretch(2)
        inner.addWidget(icon, 0, Qt.AlignmentFlag.AlignCenter)
        inner.addSpacing(6)
        inner.addWidget(title)
        inner.addWidget(subtitle, 0, Qt.AlignmentFlag.AlignCenter)
        inner.addStretch(3)

        return container

    def load_rows(self, rows: list[QTORow]):
        self._rows = rows
        self._rebuild_filters()
        self._apply_filters()
        self._stack.setCurrentIndex(1 if rows else 0)

    def append_row(self, row: QTORow):
        self._rows.append(row)
        self._rebuild_filters()
        self._apply_filters()
        self._stack.setCurrentIndex(1)

    def _rebuild_filters(self):
        trades = sorted(set(r.trade_division for r in self._rows if r.trade_division and not r.is_header_row))
        sheets = sorted(set(r.source_sheet for r in self._rows if r.source_sheet))

        self._trade_filter.blockSignals(True)
        current_trade = self._trade_filter.currentText()
        self._trade_filter.clear()
        self._trade_filter.addItem("All Trades")
        self._trade_filter.addItems(trades)
        idx = self._trade_filter.findText(current_trade)
        if idx >= 0:
            self._trade_filter.setCurrentIndex(idx)
        self._trade_filter.blockSignals(False)

        self._sheet_filter.blockSignals(True)
        current_sheet = self._sheet_filter.currentText()
        self._sheet_filter.clear()
        self._sheet_filter.addItem("All Sheets")
        self._sheet_filter.addItems(sheets)
        idx = self._sheet_filter.findText(current_sheet)
        if idx >= 0:
            self._sheet_filter.setCurrentIndex(idx)
        self._sheet_filter.blockSignals(False)

    def _apply_filters(self):
        trade = self._trade_filter.currentText()
        sheet = self._sheet_filter.currentText()
        keyword = self._keyword_filter.text().lower()
        review_only = self._review_filter.isChecked()

        self._filtered_indices = []
        for i, row in enumerate(self._rows):
            if row.is_header_row:
                self._filtered_indices.append(i)
                continue
            if trade != "All Trades" and row.trade_division != trade:
                continue
            if sheet != "All Sheets" and row.source_sheet != sheet:
                continue
            if keyword and keyword not in row.description.lower():
                continue
            if review_only and not row.needs_review:
                continue
            self._filtered_indices.append(i)

        self._render()

    def _render(self):
        self._table.setRowCount(0)
        for idx in self._filtered_indices:
            row = self._rows[idx]
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setRowHeight(r, 24 if not row.is_header_row else 28)

            cells = [
                ("" if row.is_header_row else (str(row.s_no) if row.s_no else "")),
                row.drawings,
                row.details,
                row.description,
                ("" if row.is_header_row else (str(row.qty) if row.qty else "")),
                row.units,
                "",   # Unit price — always blank
                "",   # Total formula (not editable in UI)
            ]

            for c, val in enumerate(cells):
                item = QTableWidgetItem(str(val) if val else "")
                item.setData(Qt.ItemDataRole.UserRole, idx)

                if row.is_header_row:
                    item.setBackground(QBrush(_SECTION_BG_COLOR))
                    item.setForeground(QBrush(_TEXT_1_COLOR))
                    font = QFont()
                    font.setBold(True)
                    item.setFont(font)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                else:
                    if c not in _COL_EDITABLE:
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    else:
                        item.setFlags(
                            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
                        )

                    if row.needs_review and c == 0:
                        item.setForeground(QBrush(QColor(AMBER)))

                self._table.setItem(r, c, item)

            # Amber left border indicator for needs_review rows
            if row.needs_review and not row.is_header_row:
                self._table.item(r, 0).setBackground(QBrush(QColor("#1A160A")))

    def _context_menu(self, pos: QPoint):
        item = self._table.itemAt(pos)
        if not item:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return
        row = self._rows[idx]

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {SURFACE_2}; color: {TEXT_1}; border: 1px solid {BORDER_HEX}; }}"
            f"QMenu::item:selected {{ background: {INDIGO}; }}"
        )

        delete_act = QAction("Delete Row", menu)
        add_act = QAction("Add Row Below", menu)
        review_act = QAction("Mark as Reviewed", menu)
        jump_act = QAction(f"Jump to PDF Page {row.source_page}", menu)
        save_assembly_act = QAction("Save as Assembly…", menu)

        delete_act.triggered.connect(lambda: self._delete_row(idx))
        add_act.triggered.connect(lambda: self._add_row_below(idx))
        review_act.triggered.connect(lambda: self._mark_reviewed(idx))
        jump_act.triggered.connect(lambda: self.row_jump_requested.emit(row.source_page, row.source_sheet))
        save_assembly_act.triggered.connect(lambda: self.save_as_assembly_requested.emit(idx))

        if not row.is_header_row:
            menu.addAction(delete_act)
        menu.addAction(add_act)
        if row.needs_review:
            menu.addAction(review_act)
        if row.source_page:
            menu.addAction(jump_act)
        if not row.is_header_row:
            menu.addAction(save_assembly_act)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _delete_row(self, idx: int):
        self._rows.pop(idx)
        self._apply_filters()

    def _add_row_below(self, idx: int):
        new_row = QTORow(description="", units="EA", needs_review=True)
        self._rows.insert(idx + 1, new_row)
        self._apply_filters()

    def _mark_reviewed(self, idx: int):
        self._rows[idx].needs_review = False
        self._apply_filters()

    def get_rows(self) -> list[QTORow]:
        return self._rows

    def selected_data_row(self) -> Optional[QTORow]:
        """Return the currently-selected non-header row, or ``None``."""
        items = self._table.selectedItems()
        if not items:
            return None
        for item in items:
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is None:
                continue
            row = self._rows[idx]
            if not row.is_header_row:
                return row
        return None

    def row_at_index(self, idx: int) -> Optional[QTORow]:
        if 0 <= idx < len(self._rows):
            return self._rows[idx]
        return None
