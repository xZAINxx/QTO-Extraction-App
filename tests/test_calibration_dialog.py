"""Wave 6 commit 11 — CalibrationDialog tests.

Headless / module-scoped ``QApplication`` pattern. Persistence tests
use ``tmp_path`` so the on-disk cache stays scoped to the run.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_calibration_dialog_construction(qapp, tmp_path) -> None:
    from ui.panels.calibration_dialog import CalibrationDialog

    dlg = CalibrationDialog(
        sheets=["A-101", "S-201"],
        cache_dir=tmp_path,
        pdf_fingerprint="x.pdf:100",
    )
    try:
        assert dlg.windowTitle() == "Calibrate Scale"
        combo = dlg.findChild(object, "calibrationSheetCombo")
        assert combo is not None
        assert combo.count() == 2
        assert combo.itemText(0) == "A-101"
        assert combo.isEnabled() is True
    finally:
        dlg.deleteLater()


def test_calibration_dialog_handles_empty_sheet_list(qapp, tmp_path) -> None:
    from ui.panels.calibration_dialog import CalibrationDialog

    dlg = CalibrationDialog(sheets=[], cache_dir=tmp_path)
    try:
        combo = dlg.findChild(object, "calibrationSheetCombo")
        assert combo is not None
        assert combo.isEnabled() is False
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# Scale math
# ---------------------------------------------------------------------------


def test_calibration_dialog_calculates_scale_from_pixel_and_real_distance(
    qapp, tmp_path,
) -> None:
    from ui.panels.calibration_dialog import CalibrationDialog

    dlg = CalibrationDialog(sheets=["A-101"], cache_dir=tmp_path)
    try:
        dlg._pixel_spin.setValue(400)
        dlg._real_spin.setValue(10.0)
        idx = dlg._units_combo.findText("FT")
        dlg._units_combo.setCurrentIndex(idx)
        # 10 / 400 == 0.025 FT/px
        assert dlg.compute_scale() == pytest.approx(0.025)
        preview = dlg.findChild(object, "calibrationScaleLabel")
        assert preview is not None
        assert "0.025" in preview.text()
        assert "FT/px" in preview.text()
    finally:
        dlg.deleteLater()


def test_calibration_dialog_handles_zero_pixel_distance(qapp, tmp_path) -> None:
    from ui.panels.calibration_dialog import CalibrationDialog

    dlg = CalibrationDialog(sheets=["A-101"], cache_dir=tmp_path)
    try:
        # Pixel spin is bounded to >= 1 by the QSpinBox range; set the
        # minimum and confirm no divide-by-zero leaks into the preview.
        dlg._pixel_spin.setValue(1)
        dlg._real_spin.setValue(5.0)
        assert dlg.compute_scale() == pytest.approx(5.0)
        label = dlg._scale_label.text()
        assert "inf" not in label.lower()
        assert "nan" not in label.lower()
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# Propagate-to-series
# ---------------------------------------------------------------------------


def test_calibration_dialog_propagate_finds_sheets_in_same_series(
    qapp, tmp_path,
) -> None:
    from ui.panels.calibration_dialog import (
        CalibrationDialog, _sheets_in_same_series,
    )

    sheets = ["A-101", "A-102", "A-201", "S-101"]
    affected = _sheets_in_same_series("A-101", sheets)
    assert sorted(affected) == ["A-101", "A-102", "A-201"]
    assert "S-101" not in affected

    # End-to-end via the dialog: pick A-101, click propagate, capture
    # the sheets the dialog committed against.
    dlg = CalibrationDialog(sheets=sheets, cache_dir=tmp_path)
    try:
        dlg._sheet_combo.setCurrentText("A-101")
        dlg._propagate = True
        assert sorted(dlg.selected_sheets()) == ["A-101", "A-102", "A-201"]
    finally:
        dlg.deleteLater()


def test_calibration_dialog_apply_one_sheet_only(qapp, tmp_path) -> None:
    from ui.panels.calibration_dialog import CalibrationDialog

    dlg = CalibrationDialog(sheets=["A-101", "A-102"], cache_dir=tmp_path)
    try:
        dlg._sheet_combo.setCurrentText("A-101")
        dlg._propagate = False
        assert dlg.selected_sheets() == ["A-101"]
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_calibration_dialog_persists_to_json(qapp, tmp_path) -> None:
    from ui.panels.calibration_dialog import CalibrationDialog

    fingerprint = "alpha.pdf:1024"

    # Apply a calibration via the commit path, then verify the JSON.
    dlg = CalibrationDialog(
        sheets=["A-101", "A-102"],
        cache_dir=tmp_path,
        pdf_fingerprint=fingerprint,
    )
    try:
        dlg._sheet_combo.setCurrentText("A-101")
        dlg._pixel_spin.setValue(200)
        dlg._real_spin.setValue(5.0)
        idx = dlg._units_combo.findText("FT")
        dlg._units_combo.setCurrentIndex(idx)
        dlg._propagate = False
        dlg._commit()
    finally:
        dlg.deleteLater()

    path = tmp_path / "calibration.json"
    assert path.exists()
    blob = json.loads(path.read_text())
    assert fingerprint in blob
    record = blob[fingerprint]["A-101"]
    assert record["scale"] == pytest.approx(0.025)
    assert record["units"] == "FT"

    # Recreate the dialog with the same fingerprint and confirm load.
    dlg2 = CalibrationDialog(
        sheets=["A-101", "A-102"],
        cache_dir=tmp_path,
        pdf_fingerprint=fingerprint,
    )
    try:
        loaded = dlg2._store.data.get("A-101", {})
        assert float(loaded.get("scale", 0)) == pytest.approx(0.025)
        assert loaded.get("units") == "FT"
    finally:
        dlg2.deleteLater()


# ---------------------------------------------------------------------------
# Signal emission
# ---------------------------------------------------------------------------


def test_calibration_dialog_emits_signal_on_apply(qapp, tmp_path) -> None:
    from ui.panels.calibration_dialog import CalibrationDialog

    dlg = CalibrationDialog(
        sheets=["A-101", "A-102", "S-101"],
        cache_dir=tmp_path,
        pdf_fingerprint="x.pdf:1",
    )
    captured: list[tuple] = []
    dlg.calibration_applied.connect(
        lambda s, scale, units: captured.append((list(s), float(scale), str(units)))
    )
    try:
        dlg._sheet_combo.setCurrentText("A-101")
        dlg._pixel_spin.setValue(100)
        dlg._real_spin.setValue(2.0)
        idx = dlg._units_combo.findText("M")
        dlg._units_combo.setCurrentIndex(idx)
        # Propagate path — should fire with [A-101, A-102] only.
        dlg._on_propagate()
        assert len(captured) == 1
        sheets, scale, units = captured[0]
        assert sorted(sheets) == ["A-101", "A-102"]
        assert scale == pytest.approx(0.02)
        assert units == "M"
    finally:
        dlg.deleteLater()
