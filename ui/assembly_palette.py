"""Drag-and-drop assembly palette — Phase 3 sidebar widget.

Lists every assembly grouped by trade. The user picks one, fills in
inputs in :class:`AssemblyInputDialog`, and the produced :class:`QTORow`
is emitted via the ``row_created`` signal so :class:`MainWindow` can
append it to the active takeoff.

Zero API tokens — all rendering happens locally in
:meth:`core.assembly_engine.Assembly.apply`.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.assembly_engine import Assembly, AssemblyEngine, AssemblyInput
from core.qto_row import QTORow
from ui.theme import (
    BORDER_HEX,
    INDIGO,
    SURFACE_1,
    SURFACE_2,
    SURFACE_3,
    TEXT_1,
    TEXT_2,
    TEXT_3,
)


class AssemblyInputDialog(QDialog):
    """Modal that gathers inputs for one assembly and previews the description."""

    def __init__(self, assembly: Assembly, parent: Optional[QWidget] = None,
                 default_sheet: str = ""):
        super().__init__(parent)
        self._assembly = assembly
        self._sheet = default_sheet
        self._field_widgets: dict[str, QWidget] = {}

        self.setWindowTitle(f"Assembly — {assembly.name}")
        self.setMinimumWidth(440)
        self._build_ui()
        self._update_preview()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        title = QLabel(self._assembly.name)
        f = QFont()
        f.setBold(True)
        f.setPointSize(13)
        title.setFont(f)
        root.addWidget(title)

        meta = QLabel(
            f"<span style='color:{TEXT_3}'>"
            f"{self._assembly.trade.upper()} · {self._assembly.csi_division or 'no CSI'}"
            f" · {self._assembly.units}"
            f"</span>"
        )
        meta.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(meta)

        form = QFormLayout()
        form.setSpacing(6)

        sheet_field = QLineEdit(self._sheet)
        sheet_field.setPlaceholderText("e.g. A-106")
        sheet_field.textChanged.connect(self._update_preview)
        self._sheet_field = sheet_field
        form.addRow("Sheet", sheet_field)

        for inp in self._assembly.inputs:
            widget = self._build_input(inp)
            self._field_widgets[inp.name] = widget
            form.addRow(inp.label, widget)
        root.addLayout(form)

        preview_label = QLabel("Description preview:")
        preview_label.setStyleSheet(f"color: {TEXT_2}; font-size: 11px;")
        root.addWidget(preview_label)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMinimumHeight(120)
        self._preview.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE_3}; color: {TEXT_1}; "
            f"border: 1px solid {BORDER_HEX}; border-radius: 6px; padding: 6px; "
            f"font-family: 'SF Mono', 'Monaco', 'Menlo', monospace; font-size: 12px; }}"
        )
        root.addWidget(self._preview)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_input(self, inp: AssemblyInput) -> QWidget:
        if inp.type == "number":
            w = QDoubleSpinBox()
            w.setMinimum(0)
            w.setMaximum(1_000_000)
            w.setDecimals(2)
            try:
                w.setValue(float(inp.default or 0))
            except (TypeError, ValueError):
                w.setValue(0)
            w.valueChanged.connect(self._update_preview)
            return w
        if inp.type == "select":
            w = QComboBox()
            options = list(inp.options) or [str(inp.default)]
            w.addItems([str(o) for o in options])
            if inp.default in options:
                w.setCurrentText(str(inp.default))
            w.currentTextChanged.connect(self._update_preview)
            return w
        # default: text
        w = QLineEdit(str(inp.default or ""))
        w.textChanged.connect(self._update_preview)
        return w

    def _collect_values(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for name, widget in self._field_widgets.items():
            if isinstance(widget, QDoubleSpinBox):
                out[name] = widget.value()
            elif isinstance(widget, QComboBox):
                out[name] = widget.currentText()
            elif isinstance(widget, QLineEdit):
                out[name] = widget.text()
        return out

    def _update_preview(self) -> None:
        try:
            row = self._assembly.apply(
                self._collect_values(),
                sheet=self._sheet_field.text(),
            )
            self._preview.setPlainText(row.description)
        except Exception as e:  # pragma: no cover — defensive
            self._preview.setPlainText(f"[preview failed: {e}]")

    def to_qto_row(self) -> QTORow:
        return self._assembly.apply(
            self._collect_values(),
            sheet=self._sheet_field.text(),
        )


class AssemblyPalette(QWidget):
    """Sidebar widget that lists assemblies grouped by trade.

    Signals
    -------
    row_created
        Emitted with a fully-populated :class:`QTORow` whenever the user
        accepts the input dialog. :class:`MainWindow` should append the
        row to its active takeoff.
    """

    row_created = pyqtSignal(QTORow)
    save_requested = pyqtSignal()  # raised by the "+ New" button

    def __init__(self, engine: Optional[AssemblyEngine] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._engine = engine or AssemblyEngine()
        self._default_sheet = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("Assemblies")
        f = QFont()
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {TEXT_1};")
        header.addWidget(title)
        header.addStretch()
        new_btn = QPushButton("+")
        new_btn.setFixedWidth(28)
        new_btn.setToolTip("Save the selected results-table row as a new assembly")
        new_btn.clicked.connect(self.save_requested.emit)
        header.addWidget(new_btn)
        layout.addLayout(header)

        search = QLineEdit()
        search.setPlaceholderText("Search assemblies…")
        search.textChanged.connect(self._apply_search)
        self._search = search
        layout.addWidget(search)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setStyleSheet(
            f"QTreeWidget {{ background: {SURFACE_1}; color: {TEXT_1}; "
            f"border: 1px solid {BORDER_HEX}; border-radius: 6px; }}"
            f"QTreeWidget::item {{ padding: 4px 6px; }}"
            f"QTreeWidget::item:selected {{ background: {INDIGO}; color: {TEXT_1}; }}"
        )
        self._tree.itemDoubleClicked.connect(self._open_dialog)
        layout.addWidget(self._tree, 1)

        self._refresh_tree()

    # ── Public API ─────────────────────────────────────────────────────────

    def set_default_sheet(self, sheet: str) -> None:
        self._default_sheet = sheet or ""

    def reload(self) -> None:
        self._engine.reload()
        self._refresh_tree()

    def engine(self) -> AssemblyEngine:
        return self._engine

    # ── Internals ──────────────────────────────────────────────────────────

    def _refresh_tree(self) -> None:
        self._tree.clear()
        for trade, items in self._engine.by_trade().items():
            parent = QTreeWidgetItem([trade.upper()])
            f = QFont()
            f.setBold(True)
            parent.setFont(0, f)
            self._tree.addTopLevelItem(parent)
            for asm in items:
                child = QTreeWidgetItem([asm.name])
                child.setData(0, Qt.ItemDataRole.UserRole, asm.key)
                child.setToolTip(0, f"{asm.units} · {asm.csi_division}")
                parent.addChild(child)
            parent.setExpanded(True)

    def _apply_search(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(i)
            visible_children = 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                hit = (not text) or (text in child.text(0).lower())
                child.setHidden(not hit)
                if hit:
                    visible_children += 1
            parent.setHidden(visible_children == 0)
            parent.setExpanded(bool(text) or visible_children > 0)

    def _open_dialog(self, item: QTreeWidgetItem) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key:  # parent (trade) row
            return
        try:
            assembly = self._engine.get(key)
        except KeyError:
            return
        dlg = AssemblyInputDialog(assembly, self, default_sheet=self._default_sheet)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.row_created.emit(dlg.to_qto_row())
