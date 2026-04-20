"""PDF drag-and-drop upload panel with project metadata form."""
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFormLayout, QFrame, QSpinBox, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from ui.theme import SURFACE_2, SURFACE_3, TEXT_1, TEXT_2, TEXT_3, BORDER_HEX, INDIGO, AMBER


class DropZone(QFrame):
    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setStyleSheet(
            f"QFrame#dropZone {{ background: {SURFACE_2}; border: 2px dashed {BORDER_HEX}; "
            f"border-radius: 8px; }}"
            f"QFrame#dropZone:hover {{ border-color: {INDIGO}; }}"
        )
        self.setMinimumHeight(80)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("📄")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 24px; background: transparent;")
        label = QLabel("Drop PDF here or click Browse")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(f"color: {TEXT_2}; font-size: 12px; background: transparent;")

        layout.addWidget(icon)
        layout.addWidget(label)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setStyleSheet(
                f"QFrame#dropZone {{ background: {SURFACE_3}; border: 2px solid {INDIGO}; "
                f"border-radius: 8px; }}"
            )

    def dragLeaveEvent(self, e):
        self.setStyleSheet(
            f"QFrame#dropZone {{ background: {SURFACE_2}; border: 2px dashed {BORDER_HEX}; "
            f"border-radius: 8px; }}"
        )

    def dropEvent(self, e: QDropEvent):
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith(".pdf"):
                self.file_dropped.emit(path)
        self.setStyleSheet(
            f"QFrame#dropZone {{ background: {SURFACE_2}; border: 2px dashed {BORDER_HEX}; "
            f"border-radius: 8px; }}"
        )

    def mousePressEvent(self, e):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF Files (*.pdf)")
        if path:
            self.file_dropped.emit(path)


class UploadPanel(QWidget):
    pdf_selected = pyqtSignal(str)
    project_meta_changed = pyqtSignal(dict)

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
