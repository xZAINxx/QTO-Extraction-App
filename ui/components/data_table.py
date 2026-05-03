"""DataTable — virtualizable QTORow grid (QTableView + QAbstractTableModel).

Wave 2 commit 5 of the dapper-pebble plan. Replaces the legacy
``ui/results_table.py`` (``QTableWidget``-based) with a model/view stack
that virtualizes 10k+ rows without losing the existing row interactions.

Public surface
--------------

* :class:`QtoTableModel`        — 9-column model over ``list[QTORow]``.
* :class:`StatusPillDelegate`   — paint-only StatusPill renderer for column 8.
* :class:`QtoDataTable`         — composite widget with filter bar, empty state,
  Y-key shortcut for yellow-confirm, and the public signal API consumed by
  the takeoff workspace.
"""
from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QRect,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.qto_row import QTORow
from ui.components.button import Button
from ui.components.empty_state import EmptyState
from ui.theme import tokens


# ---------------------------------------------------------------------------
# Column layout — kept as module constants so the delegate, the model, and
# the table view share the same indices without re-declaring them.
# ---------------------------------------------------------------------------

COL_S_NO = 0
COL_DRAWINGS = 1
COL_TAG = 2
COL_DESCRIPTION = 3
COL_QTY = 4
COL_UNITS = 5
COL_UNIT_PRICE = 6
COL_TOTAL = 7
COL_STATUS = 8

_COLUMNS: tuple[str, ...] = (
    "S.NO",
    "DRAWINGS",
    "TAG",
    "DESCRIPTION",
    "QTY",
    "UNITS",
    "UNIT PRICE",
    "TOTAL",
    "STATUS",
)

_EDITABLE_COLUMNS: frozenset[int] = frozenset(
    {COL_DESCRIPTION, COL_QTY, COL_UNITS, COL_UNIT_PRICE}
)
_NUMERIC_COLUMNS: frozenset[int] = frozenset(
    {COL_QTY, COL_UNIT_PRICE, COL_TOTAL}
)

# Custom roles — well above Qt.UserRole to avoid collision with built-ins.
STATUS_ROLE: int = Qt.ItemDataRole.UserRole + 1
BBOX_ROLE: int = Qt.ItemDataRole.UserRole + 2
PAGE_ROLE: int = Qt.ItemDataRole.UserRole + 3
ROW_OBJECT_ROLE: int = Qt.ItemDataRole.UserRole + 4

# Column widths (px) for the view. STRETCH_COLUMN gets every leftover px.
_COLUMN_WIDTHS: dict[int, int] = {
    COL_S_NO: 50,
    COL_DRAWINGS: 140,
    COL_TAG: 50,
    # COL_DESCRIPTION → stretch
    COL_QTY: 70,
    COL_UNITS: 70,
    COL_UNIT_PRICE: 90,
    COL_TOTAL: 90,
    COL_STATUS: 140,
}
STRETCH_COLUMN = COL_DESCRIPTION
_ROW_HEIGHT_PX = 28

# Confirmed-row tint: domain yellow at ~18% alpha. We render this in the
# delegate as well as via BackgroundRole because some Qt styles paint the
# selection brush over the BackgroundRole; keeping both keeps the visual
# language readable in any theme.
def _confirmed_brush() -> QBrush:
    color = QColor(tokens["color"]["confirmed-yellow"])
    color.setAlpha(46)  # ~18%
    return QBrush(color)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class QtoTableModel(QAbstractTableModel):
    """Model exposing a ``list[QTORow]`` to a ``QTableView``.

    Roles:
        DisplayRole     — formatted string for each column.
        EditRole        — raw value (float for numerics, str otherwise).
        TextAlignmentRole — right-align numeric columns.
        BackgroundRole  — yellow tint when ``row.confirmed`` is True.
        STATUS_ROLE     — confidence float, for the StatusPill delegate.
        BBOX_ROLE       — row.bbox tuple (any column).
        PAGE_ROLE       — row.source_page int (any column).
        ROW_OBJECT_ROLE — direct ``QTORow`` reference, debugging convenience.
    """

    rowConfirmed = pyqtSignal(int, bool)

    def __init__(
        self,
        rows: list[QTORow] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows: list[QTORow] = list(rows) if rows else []

    # --- Qt overrides --------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(_COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(_COLUMNS)
        ):
            return _COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        # Header rows in the legacy table are read-only; we mirror that.
        row = self._rows[index.row()]
        if row.is_header_row:
            return base
        if index.column() in _EDITABLE_COLUMNS:
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if not index.isValid():
            return None
        if not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        col = index.column()

        if role == ROW_OBJECT_ROLE:
            return row
        if role == STATUS_ROLE:
            return float(row.confidence)
        if role == BBOX_ROLE:
            return row.bbox
        if role == PAGE_ROLE:
            return int(row.source_page)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in _NUMERIC_COLUMNS:
                return int(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            if col == COL_STATUS:
                return int(
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                )
            return int(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        if role == Qt.ItemDataRole.BackgroundRole:
            if row.confirmed:
                return _confirmed_brush()
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(row, col)
        if role == Qt.ItemDataRole.EditRole:
            return self._edit_value(row, col)
        return None

    def setData(
        self,
        index: QModelIndex,
        value,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid():
            return False
        if role != Qt.ItemDataRole.EditRole:
            return False
        col = index.column()
        if col not in _EDITABLE_COLUMNS:
            return False
        row = self._rows[index.row()]
        if row.is_header_row:
            return False
        if col == COL_DESCRIPTION:
            row.description = str(value) if value is not None else ""
        elif col == COL_QTY:
            parsed = _to_float(value)
            if parsed is None:
                return False
            row.qty = parsed
        elif col == COL_UNITS:
            row.units = str(value) if value is not None else ""
        elif col == COL_UNIT_PRICE:
            parsed = _to_float(value)
            if parsed is None:
                return False
            row.unit_price = parsed
        else:  # pragma: no cover — guarded by _EDITABLE_COLUMNS check above
            return False
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
        return True

    # --- public API ----------------------------------------------------------

    def rows(self) -> list[QTORow]:
        return self._rows

    def row_at(self, source_row: int) -> QTORow:
        return self._rows[source_row]

    def replace_rows(self, new_rows: Iterable[QTORow]) -> None:
        self.beginResetModel()
        self._rows = list(new_rows)
        self.endResetModel()

    def add_row(self, row: QTORow, at: int | None = None) -> None:
        position = len(self._rows) if at is None else max(0, min(at, len(self._rows)))
        self.beginInsertRows(QModelIndex(), position, position)
        self._rows.insert(position, row)
        self.endInsertRows()

    def remove_row(self, source_row: int) -> bool:
        if not (0 <= source_row < len(self._rows)):
            return False
        self.beginRemoveRows(QModelIndex(), source_row, source_row)
        self._rows.pop(source_row)
        self.endRemoveRows()
        return True

    def set_confirmed(self, source_row: int, confirmed: bool) -> bool:
        if not (0 <= source_row < len(self._rows)):
            return False
        row = self._rows[source_row]
        if row.confirmed == confirmed:
            return False
        row.confirmed = confirmed
        left = self.index(source_row, 0)
        right = self.index(source_row, self.columnCount() - 1)
        # Fire BackgroundRole alongside Display/Edit so the whole row repaints.
        self.dataChanged.emit(
            left,
            right,
            [
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.DisplayRole,
                STATUS_ROLE,
            ],
        )
        self.rowConfirmed.emit(source_row, confirmed)
        return True

    def mark_reviewed(self, source_row: int) -> bool:
        if not (0 <= source_row < len(self._rows)):
            return False
        row = self._rows[source_row]
        row.needs_review = False
        # Bump confidence to "above review threshold" so the StatusPill
        # paints green next time. Mirrors the legacy "Mark as Reviewed".
        row.confidence = max(row.confidence, 0.75)
        left = self.index(source_row, 0)
        right = self.index(source_row, self.columnCount() - 1)
        self.dataChanged.emit(
            left, right,
            [Qt.ItemDataRole.DisplayRole, STATUS_ROLE],
        )
        return True

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _display_value(row: QTORow, col: int) -> str:
        if col == COL_S_NO:
            return "" if row.is_header_row else (str(row.s_no) if row.s_no else "")
        if col == COL_DRAWINGS:
            return row.drawings or ""
        if col == COL_TAG:
            return row.tag or ""
        if col == COL_DESCRIPTION:
            return row.description or ""
        if col == COL_QTY:
            if row.is_header_row:
                return ""
            if not row.qty:
                return ""
            return _format_number(row.qty)
        if col == COL_UNITS:
            return row.units or ""
        if col == COL_UNIT_PRICE:
            if not row.unit_price:
                return ""
            return _format_number(row.unit_price)
        if col == COL_TOTAL:
            if row.is_header_row:
                return ""
            total = (row.qty or 0.0) * (row.unit_price or 0.0)
            if not total:
                return ""
            return _format_number(total)
        if col == COL_STATUS:
            # The visible string is also drawn by the delegate; keeping it
            # here means the column is still readable if the delegate is
            # ever swapped out (e.g. exported to plain QTableView).
            percent = max(0, min(100, int(round(row.confidence * 100))))
            return f"{percent}%"
        return ""

    @staticmethod
    def _edit_value(row: QTORow, col: int):
        if col == COL_DESCRIPTION:
            return row.description or ""
        if col == COL_QTY:
            return float(row.qty or 0.0)
        if col == COL_UNITS:
            return row.units or ""
        if col == COL_UNIT_PRICE:
            return float(row.unit_price or 0.0)
        if col == COL_TOTAL:
            return float((row.qty or 0.0) * (row.unit_price or 0.0))
        return QtoTableModel._display_value(row, col)


def _to_float(value) -> float | None:
    """Best-effort numeric parse; returns None if the value can't be parsed."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    # Strip thousands separators / dollar signs the user might paste in.
    cleaned = text.replace(",", "").replace("$", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


# ---------------------------------------------------------------------------
# StatusPill delegate — paints the pill via QPainter primitives.
# ---------------------------------------------------------------------------


def _classify_confidence(confidence: float) -> tuple[str, str]:
    """Return (color_token_key, label) — mirror StatusPill widget rules."""
    if confidence >= 0.9:
        return ("approved-green", "Confirm")
    if confidence >= 0.6:
        return ("warning", "Review")
    return ("danger", "Re-extract")


class StatusPillDelegate(QStyledItemDelegate):
    """Paints a confidence pill in the STATUS column.

    A real ``StatusPill`` widget per cell would defeat virtualization
    (Qt instantiates the delegate once and reuses it). So we paint the pill
    primitives directly: rounded background, percent + label text. Clicking
    inside the pill rect emits :pyattr:`confirmRequested` with the row's
    *source-model* index.
    """

    confirmRequested = pyqtSignal(int)
    reviewRequested = pyqtSignal(int)
    reextractRequested = pyqtSignal(int)

    _PAD_X = 8
    _RADIUS = 10

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        if index.column() != COL_STATUS:
            super().paint(painter, option, index)
            return

        confidence = index.data(STATUS_ROLE)
        if confidence is None:
            super().paint(painter, option, index)
            return

        # Paint default cell background (selection / alternation) first.
        super().paint(painter, option, index)

        token_key, label = _classify_confidence(float(confidence))
        bg = QColor(tokens["color"][token_key])
        bg.setAlpha(46)  # subtle fill
        fg = QColor(tokens["color"][token_key])

        rect = option.rect.adjusted(
            self._PAD_X, 4, -self._PAD_X, -4
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(bg)
        pen = QPen(fg)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)

        percent = max(0, min(100, int(round(float(confidence) * 100))))
        text_color = QColor(tokens["color"][token_key])
        painter.setPen(text_color)
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
            f"{percent}% · {label}",
        )
        painter.restore()

    def editorEvent(
        self,
        event,
        model,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        if index.column() != COL_STATUS:
            return super().editorEvent(event, model, option, index)
        if (
            isinstance(event, QMouseEvent)
            and event.type() == QMouseEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            confidence = index.data(STATUS_ROLE)
            if confidence is None:
                return False
            source_row = self._source_row(index)
            _, label = _classify_confidence(float(confidence))
            if label == "Confirm":
                self.confirmRequested.emit(source_row)
            elif label == "Review":
                self.reviewRequested.emit(source_row)
            else:
                self.reextractRequested.emit(source_row)
            return True
        return super().editorEvent(event, model, option, index)

    @staticmethod
    def _source_row(index: QModelIndex) -> int:
        # If a proxy model is in front of the source, climb back to the
        # source-model index so callers see consistent indices regardless
        # of filter/sort state.
        model = index.model()
        if isinstance(model, QSortFilterProxyModel):
            return model.mapToSource(index).row()
        return index.row()


# ---------------------------------------------------------------------------
# Filter proxy — AND-composed multi-column filter.
# ---------------------------------------------------------------------------


class _QtoFilterProxy(QSortFilterProxyModel):
    """Composes trade + sheet + keyword + needs-review filters with AND."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._trade: str | None = None
        self._sheet: str | None = None
        self._keyword: str = ""
        self._review_only: bool = False

    def setTradeFilter(self, trade: str | None) -> None:
        self._trade = trade or None
        self.invalidateFilter()

    def setSheetFilter(self, sheet: str | None) -> None:
        self._sheet = sheet or None
        self.invalidateFilter()

    def setKeywordFilter(self, keyword: str | None) -> None:
        self._keyword = (keyword or "").strip().lower()
        self.invalidateFilter()

    def setNeedsReviewOnly(self, on: bool) -> None:
        self._review_only = bool(on)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if not isinstance(model, QtoTableModel):
            return True
        if not (0 <= source_row < model.rowCount()):
            return False
        row = model.row_at(source_row)
        # Header rows are always shown — they're the section dividers.
        if row.is_header_row:
            return True
        if self._trade and (row.trade_division or "") != self._trade:
            return False
        if self._sheet and (row.source_sheet or "") != self._sheet:
            return False
        if self._keyword and self._keyword not in (row.description or "").lower():
            return False
        if self._review_only and not row.needs_review:
            return False
        return True


# ---------------------------------------------------------------------------
# Composite widget
# ---------------------------------------------------------------------------


class QtoDataTable(QWidget):
    """Filter bar + virtualized DataTable + empty state."""

    row_jump_requested = pyqtSignal(int, str)
    save_as_assembly_requested = pyqtSignal(int)
    rows_confirmed = pyqtSignal(list)
    review_requested = pyqtSignal(int)
    reextract_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._model = QtoTableModel(parent=self)
        self._proxy = _QtoFilterProxy(parent=self)
        self._proxy.setSourceModel(self._model)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(tokens["space"][2])

        # ---- filter bar ---------------------------------------------------
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(tokens["space"][2])

        self._trade_filter = QComboBox(self)
        self._trade_filter.addItem("All Trades")
        self._trade_filter.setFixedWidth(160)
        self._trade_filter.currentTextChanged.connect(self._on_trade_changed)

        self._sheet_filter = QComboBox(self)
        self._sheet_filter.addItem("All Sheets")
        self._sheet_filter.setFixedWidth(120)
        self._sheet_filter.currentTextChanged.connect(self._on_sheet_changed)

        self._keyword_filter = QLineEdit(self)
        self._keyword_filter.setPlaceholderText("Search description…")
        self._keyword_filter.textChanged.connect(self._proxy.setKeywordFilter)

        self._review_filter = Button(
            "Needs Review",
            variant="ghost",
            size="sm",
            parent=self,
        )
        self._review_filter.setCheckable(True)
        self._review_filter.toggled.connect(self._proxy.setNeedsReviewOnly)

        filter_bar.addWidget(QLabel("Trade:", self))
        filter_bar.addWidget(self._trade_filter)
        filter_bar.addWidget(QLabel("Sheet:", self))
        filter_bar.addWidget(self._sheet_filter)
        filter_bar.addWidget(self._keyword_filter, 1)
        filter_bar.addWidget(self._review_filter)
        outer.addLayout(filter_bar)

        # ---- stacked widget ----------------------------------------------
        self._stack = QStackedWidget(self)

        self._empty_state = EmptyState(
            icon_name="upload",
            title="No takeoff yet",
            body="Load a PDF to begin extraction.",
            parent=self,
        )
        self._stack.addWidget(self._empty_state)

        self._view = QTableView(self)
        self._view.setModel(self._proxy)
        self._view.setSortingEnabled(True)
        # Default to "no active sort" so insertion order survives until the
        # user clicks a header. setSortingEnabled defaults the proxy to
        # column 0 descending, which would silently reverse the row list.
        self._proxy.sort(-1)
        self._view.setAlternatingRowColors(True)
        self._view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._view.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._view.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        self._view.verticalHeader().setVisible(False)
        self._view.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT_PX)
        self._view.horizontalHeader().setStretchLastSection(False)

        for col, width in _COLUMN_WIDTHS.items():
            self._view.setColumnWidth(col, width)
        self._view.horizontalHeader().setSectionResizeMode(
            STRETCH_COLUMN, QHeaderView.ResizeMode.Stretch
        )

        self._delegate = StatusPillDelegate(self)
        self._view.setItemDelegateForColumn(COL_STATUS, self._delegate)
        self._delegate.confirmRequested.connect(self._on_pill_confirm)
        self._delegate.reviewRequested.connect(self._on_pill_review)
        self._delegate.reextractRequested.connect(self.reextract_requested)

        self._stack.addWidget(self._view)
        self._stack.setCurrentIndex(0)
        outer.addWidget(self._stack, 1)

        # ---- shortcuts ----------------------------------------------------
        self._confirm_shortcut = QShortcut(QKeySequence("Y"), self._view)
        self._confirm_shortcut.activated.connect(self.confirm_selected)

    # --- public API ---------------------------------------------------------

    def replace_rows(self, rows: list[QTORow]) -> None:
        self._model.replace_rows(rows)
        self._rebuild_filter_options()
        self._stack.setCurrentIndex(1 if rows else 0)

    def get_rows(self) -> list[QTORow]:
        return list(self._model.rows())

    def model(self) -> QtoTableModel:  # type: ignore[override]
        return self._model

    def proxy(self) -> _QtoFilterProxy:
        return self._proxy

    def view(self) -> QTableView:
        return self._view

    def selected_rows(self) -> list[int]:
        seen: set[int] = set()
        ordered: list[int] = []
        for index in self._view.selectionModel().selectedRows():
            source = self._proxy.mapToSource(index).row()
            if source not in seen:
                seen.add(source)
                ordered.append(source)
        return ordered

    def confirm_selected(self) -> None:
        confirmed: list[int] = []
        for source_row in self.selected_rows():
            row = self._model.row_at(source_row)
            if row.is_header_row:
                continue
            if self._model.set_confirmed(source_row, True):
                confirmed.append(source_row)
            elif row.confirmed:
                # Already confirmed — still report so the caller can persist.
                confirmed.append(source_row)
        if confirmed:
            self.rows_confirmed.emit(confirmed)

    def filter_trade(self, trade: str | None) -> None:
        self._proxy.setTradeFilter(trade)

    def filter_sheet(self, sheet: str | None) -> None:
        self._proxy.setSheetFilter(sheet)

    def filter_keyword(self, keyword: str | None) -> None:
        self._proxy.setKeywordFilter(keyword)

    def show_only_needs_review(self, on: bool) -> None:
        self._proxy.setNeedsReviewOnly(on)
        self._review_filter.setChecked(bool(on))

    # --- internals ----------------------------------------------------------

    def _on_trade_changed(self, text: str) -> None:
        self.filter_trade(None if text == "All Trades" else text)

    def _on_sheet_changed(self, text: str) -> None:
        self.filter_sheet(None if text == "All Sheets" else text)

    def _on_pill_confirm(self, source_row: int) -> None:
        if self._model.set_confirmed(source_row, True):
            self.rows_confirmed.emit([source_row])

    def _on_pill_review(self, source_row: int) -> None:
        self.review_requested.emit(source_row)

    def _rebuild_filter_options(self) -> None:
        rows = self._model.rows()
        trades = sorted(
            {r.trade_division for r in rows if r.trade_division and not r.is_header_row}
        )
        sheets = sorted(
            {r.source_sheet for r in rows if r.source_sheet}
        )
        for combo, default, values in (
            (self._trade_filter, "All Trades", trades),
            (self._sheet_filter, "All Sheets", sheets),
        ):
            combo.blockSignals(True)
            current = combo.currentText()
            combo.clear()
            combo.addItem(default)
            combo.addItems(values)
            idx = combo.findText(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _on_context_menu(self, pos) -> None:
        index = self._view.indexAt(pos)
        if not index.isValid():
            return
        source_row = self._proxy.mapToSource(index).row()
        if not (0 <= source_row < self._model.rowCount()):
            return
        row = self._model.row_at(source_row)

        menu = QMenu(self)
        if not row.is_header_row:
            act_delete = menu.addAction("Delete Row")
            act_delete.triggered.connect(lambda: self._model.remove_row(source_row))
        act_confirm = menu.addAction("Confirm (yellow)")
        act_confirm.triggered.connect(
            lambda: (self._model.set_confirmed(source_row, True), self.rows_confirmed.emit([source_row]))
        )
        if row.needs_review:
            act_review = menu.addAction("Mark as Reviewed")
            act_review.triggered.connect(lambda: self._model.mark_reviewed(source_row))
        if row.source_page:
            act_jump = menu.addAction(f"Jump to PDF Page {row.source_page}")
            act_jump.triggered.connect(
                lambda: self.row_jump_requested.emit(row.source_page, row.source_sheet)
            )
        if not row.is_header_row:
            act_assembly = menu.addAction("Save as Assembly…")
            act_assembly.triggered.connect(
                lambda: self.save_as_assembly_requested.emit(source_row)
            )
        menu.exec(self._view.viewport().mapToGlobal(pos))


__all__ = [
    "BBOX_ROLE",
    "PAGE_ROLE",
    "ROW_OBJECT_ROLE",
    "STATUS_ROLE",
    "QtoDataTable",
    "QtoTableModel",
    "StatusPillDelegate",
]
