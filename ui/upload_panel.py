"""PDF drag-and-drop upload panel with project metadata form."""
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFormLayout, QFrame, QSpinBox, QFileDialog,
    QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from ui.theme import CANVAS, SURFACE_2, SURFACE_3, TEXT_1, TEXT_2, TEXT_3, BORDER_HEX, INDIGO, AMBER


_STYLE_IDLE = (
    "QFrame#dropZone {{ background: {s2}; border: 1.5px dashed {border}; border-radius: 10px; }}"
    "QFrame#dropZone:hover {{ background: {s3}; border-color: {indigo}; }}"
)
_STYLE_DRAG = "QFrame#dropZone {{ background: {s3}; border: 1.5px solid {indigo}; border-radius: 10px; }}"


class DropZone(QFrame):
    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self._apply_idle_style()
        self.setMinimumHeight(64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(5)
        layout.setContentsMargins(12, 12, 12, 12)

        main_label = QLabel("Drop PDF or click to Browse")
        main_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_label.setStyleSheet(
            f"color: {TEXT_2}; font-size: 12px; font-weight: 600;"
            f"background: transparent; border: none;"
        )

        sub_label = QLabel("Architectural drawing sets · PDF only")
        sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_label.setStyleSheet(
            f"color: {TEXT_3}; font-size: 10px; background: transparent; border: none;"
        )

        layout.addWidget(main_label)
        layout.addWidget(sub_label)

    def _apply_idle_style(self):
        self.setStyleSheet(
            _STYLE_IDLE.format(s2=SURFACE_2, s3=SURFACE_3, border=BORDER_HEX, indigo=INDIGO)
        )

    def _apply_drag_style(self):
        self.setStyleSheet(_STYLE_DRAG.format(s3=SURFACE_3, indigo=INDIGO))

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._apply_drag_style()

    def dragLeaveEvent(self, e):
        self._apply_idle_style()

    def dropEvent(self, e: QDropEvent):
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith(".pdf"):
                self.file_dropped.emit(path)
        self._apply_idle_style()

    def mousePressEvent(self, e):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF Files (*.pdf)")
        if path:
            self.file_dropped.emit(path)


class UploadPanel(QWidget):
    pdf_selected = pyqtSignal(str)
    project_meta_changed = pyqtSignal(dict)
    cost_saver_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Section label
        lbl = QLabel("DRAWING SET")
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

        # Drop zone
        self._drop = DropZone()
        self._drop.file_dropped.connect(self._on_file)
        layout.addWidget(self._drop)

        # File info
        self._file_label = QLabel("No file selected")
        self._file_label.setObjectName("muted")
        self._file_label.setWordWrap(True)
        layout.addWidget(self._file_label)

        # Divider
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER_HEX};")
        layout.addWidget(sep)

        # Project metadata form
        meta_lbl = QLabel("PROJECT INFO")
        meta_lbl.setObjectName("sectionLabel")
        layout.addWidget(meta_lbl)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._proj_name = QLineEdit()
        self._proj_name.setPlaceholderText("Project name")
        self._proj_name.textChanged.connect(self._emit_meta)

        self._description = QLineEdit()
        self._description.setPlaceholderText("Description")
        self._description.textChanged.connect(self._emit_meta)

        self._perf_days = QSpinBox()
        self._perf_days.setRange(0, 9999)
        self._perf_days.setSuffix(" days")
        self._perf_days.valueChanged.connect(self._emit_meta)

        self._liq_damages = QLineEdit()
        self._liq_damages.setPlaceholderText("e.g. $1,000")
        self._liq_damages.textChanged.connect(self._emit_meta)

        self._bid_date = QLineEdit()
        self._bid_date.setPlaceholderText("MM/DD/YYYY")
        self._bid_date.textChanged.connect(self._emit_meta)

        form.addRow("Project:", self._proj_name)
        form.addRow("Description:", self._description)
        form.addRow("Duration:", self._perf_days)
        form.addRow("Liq. Damages:", self._liq_damages)
        form.addRow("Bid Opening:", self._bid_date)
        layout.addLayout(form)

        # Phase 7 — cost-saver toggle. Off by default: it adds a few minutes
        # of latency at the end of a run in exchange for the 50% Anthropic
        # batch discount on description-composition calls.
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {BORDER_HEX};")
        layout.addWidget(sep2)

        self._cost_saver = QCheckBox("Cost-saver mode (batch · ~50% off)")
        self._cost_saver.setToolTip(
            "Defer description-composition calls into a single Anthropic Batch "
            "request. Adds ~30 s–5 min of latency at the end of the run; cuts "
            "the per-row composition spend roughly in half. Other calls are "
            "unaffected."
        )
        self._cost_saver.setStyleSheet(
            f"color: {TEXT_2}; font-size: 11px; padding: 2px 0px;"
        )
        self._cost_saver.toggled.connect(self.cost_saver_toggled)
        layout.addWidget(self._cost_saver)

    def set_cost_saver(self, enabled: bool) -> None:
        """Programmatic setter — used to seed from config.yaml at startup."""
        self._cost_saver.blockSignals(True)
        self._cost_saver.setChecked(bool(enabled))
        self._cost_saver.blockSignals(False)

    def is_cost_saver(self) -> bool:
        return self._cost_saver.isChecked()

    def _on_file(self, path: str):
        p = Path(path)
        size_mb = p.stat().st_size / (1024 * 1024)
        self._file_label.setText(f"{p.name}\n{size_mb:.1f} MB")
        self.pdf_selected.emit(path)

    def _emit_meta(self):
        self.project_meta_changed.emit(self._get_meta())

    def _get_meta(self) -> dict:
        return {
            "project": self._proj_name.text(),
            "description": self._description.text(),
            "performance_period_days": self._perf_days.value() or None,
            "liquidated_damages": self._liq_damages.text(),
            "bid_opening_date": self._bid_date.text(),
        }
