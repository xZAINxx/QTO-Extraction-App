"""CalibrationDialog — per-sheet scale calibration with propagate-to-series.

Wave 6 commit 11 of the dapper-pebble plan (section "7. Phase 3 features"
item #13). The estimator picks a sheet, enters a known pixel distance
(``QSpinBox``) and the corresponding real-world distance (``QDoubleSpinBox``
+ units), and either applies the scale to the selected sheet or
propagates it to every sheet in the same series (e.g. all ``A-*`` sheets).

Persistence mirrors :mod:`ui.panels._scope_store` — JSON-on-disk keyed
by ``<pdf_fingerprint>``::

    {
        "<filename>:<filesize>": {
            "<sheet_id>": {"scale": float, "units": str}
        }
    }

Interactive PDF point-picking is a future enhancement; this dialog uses
the manual-entry path only. Document the deferral in the docstring so
nobody hunts for the picker hookup.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ui.theme import tokens

_UNITS: tuple[str, ...] = ("FT", "IN", "M", "MM")
_DEFAULT_PIXEL = 100
_DEFAULT_REAL = 10.0


@dataclass
class _CalibrationStore:
    """JSON-backed per-PDF calibration store. Mirrors :class:`ScopeStore`."""

    cache_dir: Path
    fingerprint: str = ""
    data: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.cache_dir / "calibration.json"

    def load(self, pdf_fingerprint: str) -> dict[str, dict[str, object]]:
        self.fingerprint = pdf_fingerprint
        if not self.path.exists():
            self.data = {}
            return self.data
        try:
            blob = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            blob = {}
        self.data = dict(blob.get(pdf_fingerprint, {}))
        return self.data

    def save(self, sheet_id: str, scale: float, units: str) -> None:
        if not self.fingerprint:
            return
        self.data[sheet_id] = {"scale": float(scale), "units": str(units)}
        self._flush()

    def save_many(self, sheet_ids: list[str], scale: float, units: str) -> None:
        if not self.fingerprint:
            return
        for sid in sheet_ids:
            self.data[sid] = {"scale": float(scale), "units": str(units)}
        self._flush()

    def _flush(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            blob = json.loads(self.path.read_text()) if self.path.exists() else {}
        except (OSError, json.JSONDecodeError):
            blob = {}
        blob[self.fingerprint] = dict(self.data)
        self.path.write_text(json.dumps(blob, indent=2))


def _series_prefix(sheet_id: str) -> str:
    """Return the discipline-letter prefix of a sheet id.

    ``"A-101"`` → ``"A"``, ``"S5.1"`` → ``"S"``, ``""`` → ``""``.
    """
    if not sheet_id:
        return ""
    head = sheet_id.strip()
    if not head:
        return ""
    return head[0].upper()


def _sheets_in_same_series(target: str, sheets: list[str]) -> list[str]:
    """Return every sheet sharing the discipline prefix of ``target``.

    The target itself is included in the returned list.
    """
    prefix = _series_prefix(target)
    if not prefix:
        return [target] if target in sheets else []
    return [s for s in sheets if _series_prefix(s) == prefix]


class CalibrationDialog(QDialog):
    """Modal scale-calibration dialog.

    Public API:
        :py:meth:`__init__(sheets, cache_dir, pdf_fingerprint, parent=None)` —
        seed the dialog with the current sheet roster, the cache dir for
        persistence, and the PDF fingerprint.
        :py:meth:`compute_scale()` — return the scale factor (real / pixel).

    Signal:
        :py:attr:`calibration_applied(list[str], float, str)` — emitted on
        accept with the affected sheet list, the scale factor, and units.
    """

    calibration_applied = pyqtSignal(list, float, str)

    def __init__(
        self,
        sheets: list[str],
        cache_dir: str | Path = "./cache",
        pdf_fingerprint: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("calibrationDialog")
        self.setWindowTitle("Calibrate Scale")
        self.setModal(True)

        self._sheets: list[str] = list(sheets or [])
        self._cache_dir = Path(cache_dir)
        self._fingerprint = pdf_fingerprint or ""
        self._store = _CalibrationStore(cache_dir=self._cache_dir)
        if self._fingerprint:
            self._store.load(self._fingerprint)
        self._propagate: bool = False

        self._build_ui()
        self._restore_existing()

    # ---- Layout ---------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        sp = tokens["space"][4]
        outer.setContentsMargins(sp, sp, sp, sp)
        outer.setSpacing(sp)

        title = QLabel("Calibrate Scale", self)
        title.setObjectName("calibrationTitle")
        title.setProperty("textSize", "h4")
        outer.addWidget(title)

        instructions = QLabel(
            "Pick a sheet, enter the pixel distance you measured, "
            "then the real-world distance and units. Interactive "
            "two-click picking on the PDF canvas is a future enhancement.",
            self,
        )
        instructions.setObjectName("calibrationInstructions")
        instructions.setProperty("textSize", "body-sm")
        instructions.setWordWrap(True)
        instructions.setStyleSheet(
            f"color: {tokens['color']['text']['secondary']};"
        )
        outer.addWidget(instructions)

        outer.addWidget(self._build_form())
        outer.addWidget(self._build_preview())
        outer.addStretch(1)
        outer.addWidget(self._build_actions())

    def _build_form(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("calibrationForm")
        form = QFormLayout(frame)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(tokens["space"][3])

        self._sheet_combo = QComboBox(frame)
        self._sheet_combo.setObjectName("calibrationSheetCombo")
        for sid in self._sheets:
            self._sheet_combo.addItem(sid)
        if not self._sheets:
            self._sheet_combo.addItem("(no sheets loaded)")
            self._sheet_combo.setEnabled(False)
        self._sheet_combo.currentTextChanged.connect(self._on_sheet_changed)
        form.addRow("Sheet:", self._sheet_combo)

        self._pixel_spin = QSpinBox(frame)
        self._pixel_spin.setObjectName("calibrationPixelSpin")
        self._pixel_spin.setRange(1, 100000)
        self._pixel_spin.setValue(_DEFAULT_PIXEL)
        self._pixel_spin.setSuffix(" px")
        self._pixel_spin.valueChanged.connect(self._refresh_preview)
        form.addRow("Pixel distance:", self._pixel_spin)

        self._real_spin = QDoubleSpinBox(frame)
        self._real_spin.setObjectName("calibrationRealSpin")
        self._real_spin.setRange(0.0001, 1_000_000.0)
        self._real_spin.setDecimals(4)
        self._real_spin.setValue(_DEFAULT_REAL)
        self._real_spin.valueChanged.connect(self._refresh_preview)
        form.addRow("Real distance:", self._real_spin)

        self._units_combo = QComboBox(frame)
        self._units_combo.setObjectName("calibrationUnitsCombo")
        for unit in _UNITS:
            self._units_combo.addItem(unit)
        self._units_combo.currentTextChanged.connect(self._refresh_preview)
        form.addRow("Units:", self._units_combo)

        return frame

    def _build_preview(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("calibrationPreview")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens["space"][3])

        label = QLabel("Computed scale:", frame)
        label.setProperty("textSize", "body")
        layout.addWidget(label)

        self._scale_label = QLabel("", frame)
        self._scale_label.setObjectName("calibrationScaleLabel")
        self._scale_label.setProperty("textSize", "body")
        self._scale_label.setStyleSheet(
            f"font-family: {tokens['font']['family']['mono']};"
        )
        layout.addWidget(self._scale_label, 1)
        return frame

    def _build_actions(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("calibrationActions")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens["space"][3])

        self._apply_one_btn = QPushButton("Apply to this sheet", frame)
        self._apply_one_btn.setObjectName("calibrationApplyOneBtn")
        self._apply_one_btn.clicked.connect(self._on_apply_one)
        layout.addWidget(self._apply_one_btn)

        self._propagate_btn = QPushButton("Propagate to series", frame)
        self._propagate_btn.setObjectName("calibrationPropagateBtn")
        self._propagate_btn.clicked.connect(self._on_propagate)
        layout.addWidget(self._propagate_btn)

        layout.addStretch(1)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel, frame,
        )
        button_box.setObjectName("calibrationDialogButtonBox")
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        return frame

    # ---- Public API -----------------------------------------------------

    def compute_scale(self) -> float:
        """Return ``real / pixel`` — the scale factor in chosen units per pixel."""
        pixel = max(1, int(self._pixel_spin.value()))
        return float(self._real_spin.value()) / float(pixel)

    def selected_sheets(self) -> list[str]:
        """Return the list of sheets that the dialog will write to.

        If propagate-to-series mode was last selected, this is every
        sheet sharing the discipline prefix; otherwise just the active
        sheet. Useful to tests asserting the propagate logic without
        firing the signal.
        """
        target = self._sheet_combo.currentText()
        if self._propagate:
            return _sheets_in_same_series(target, self._sheets)
        return [target] if target else []

    # ---- Internals ------------------------------------------------------

    def _restore_existing(self) -> None:
        self._refresh_preview()

    def _on_sheet_changed(self, _text: str) -> None:
        target = self._sheet_combo.currentText()
        existing = self._store.data.get(target)
        if existing is not None:
            try:
                self._real_spin.setValue(
                    float(self._pixel_spin.value()) * float(existing.get("scale", 0))
                )
            except (TypeError, ValueError):
                pass
            units = str(existing.get("units", ""))
            idx = self._units_combo.findText(units)
            if idx >= 0:
                self._units_combo.setCurrentIndex(idx)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        scale = self.compute_scale()
        units = self._units_combo.currentText() or "FT"
        self._scale_label.setText(
            f"{scale:.6f} {units}/px (1 {units} = {1/scale:,.2f} px)"
            if scale > 0 else "—"
        )

    def _on_apply_one(self) -> None:
        self._propagate = False
        self._commit()

    def _on_propagate(self) -> None:
        self._propagate = True
        self._commit()

    def _commit(self) -> None:
        target = self._sheet_combo.currentText()
        if not target or not self._sheet_combo.isEnabled():
            self.reject()
            return
        scale = self.compute_scale()
        units = self._units_combo.currentText() or "FT"
        affected = self.selected_sheets()
        self._store.save_many(affected, scale=scale, units=units)
        # The signal carries Python types (list/float/str); pyqtSignal's
        # generic-object marshalling handles them correctly.
        self.calibration_applied.emit(list(affected), float(scale), str(units))
        self.accept()


__all__ = ["CalibrationDialog"]
