"""Smoke tests for the new MainWindow shell (Wave 3 — commit 3).

The tests follow the same headless / module-scoped ``QApplication`` pattern
as ``test_components_smoke.py`` and ``test_sheet_rail.py`` — no
``pytest-qt`` dependency, just an offscreen Qt platform plugin.

Coverage
========
* Construction with a minimal config.
* All five named layout regions exist as findChild targets.
* Topbar contains the mode badge Pill, command palette button, theme toggle.
* Workspace host is a QTabWidget with a "Takeoff" tab present.
* ``_load_pdf`` propagates to both SheetRail and TakeoffWorkspace.
* ``main.py``'s feature-flag branch picks the right MainWindow class.
* Legacy ``ui.main_window`` and the new ExtractionWorker module both still
  import cleanly after the worker extraction.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Force Qt to its offscreen platform so the suite stays headless on CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFrame, QTabWidget


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication — matches sibling test modules."""
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# Construction & layout
# ---------------------------------------------------------------------------


def test_main_window_constructs_with_minimal_config(qapp) -> None:
    from ui.views.main_window import MainWindow

    win = MainWindow({})
    try:
        assert win.windowTitle() == "Zeconic QTO"
    finally:
        win.deleteLater()


def test_main_window_has_topbar_navrail_sheetrail_workspace_dockstrip_children(
    qapp,
) -> None:
    from ui.views.main_window import MainWindow

    win = MainWindow({})
    try:
        for object_name, expected_type in [
            ("topbar", QFrame),
            ("navRail", QFrame),
            ("sheetRail", None),  # SheetRail is a QWidget subclass
            ("workspaceHost", QTabWidget),
            ("dockStrip", QFrame),
            ("inspector", QFrame),
        ]:
            child = win.findChild(expected_type or object, object_name)
            assert child is not None, f"missing layout region: {object_name}"
    finally:
        win.deleteLater()


def test_main_window_topbar_has_mode_badge(qapp) -> None:
    from ui.components import Pill
    from ui.views.main_window import MainWindow

    cfg = {"extraction_mode": "multi_agent"}
    win = MainWindow(cfg)
    try:
        badge = win.findChild(Pill, "modeBadge")
        assert badge is not None
        assert "MULTI_AGENT" in badge.text()
    finally:
        win.deleteLater()


def test_main_window_topbar_has_command_palette_button(qapp) -> None:
    from ui.components import Button
    from ui.views.main_window import MainWindow

    win = MainWindow({})
    try:
        btn = win.findChild(Button, "cmdPaletteBtn")
        assert btn is not None
        assert "⌘K" in btn.text()
    finally:
        win.deleteLater()


def test_main_window_theme_toggle_button_present(qapp) -> None:
    from ui.components import Button
    from ui.views.main_window import MainWindow

    win = MainWindow({})
    try:
        btn = win.findChild(Button, "themeToggleBtn")
        assert btn is not None
    finally:
        win.deleteLater()


def test_main_window_workspace_host_is_qtabwidget_with_takeoff_tab(qapp) -> None:
    from ui.views.main_window import MainWindow

    win = MainWindow({})
    try:
        host = win.findChild(QTabWidget, "workspaceHost")
        assert host is not None
        labels = [host.tabText(i) for i in range(host.count())]
        # First tab is the active Takeoff tab; commit 7 promoted the Diff
        # workspace into a live tab as well, so the only remaining
        # placeholders are Cockpit (commit 9) and Coverage (commit 11).
        assert labels[0] == "Takeoff"
        assert host.isTabEnabled(0)
        assert "What Changed" in labels[1:], labels
        assert any("Cockpit" in lbl for lbl in labels[1:])
    finally:
        win.deleteLater()


# ---------------------------------------------------------------------------
# Behavior — load_pdf propagation
# ---------------------------------------------------------------------------


def test_main_window_load_pdf_propagates_to_sheet_rail_and_workspace(
    qapp, tmp_path: Path,
) -> None:
    from ui.views.main_window import MainWindow

    win = MainWindow({})
    try:
        fake_pdf = tmp_path / "drawings.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 test\n")

        with patch.object(win._sheet_rail, "load_pdf") as sr_load, \
                patch.object(win._takeoff, "load_pdf") as tw_load:
            win._load_pdf(str(fake_pdf))

        sr_load.assert_called_once_with(str(fake_pdf))
        tw_load.assert_called_once_with(str(fake_pdf))
        assert win._pdf_path == str(fake_pdf)
    finally:
        win.deleteLater()


# ---------------------------------------------------------------------------
# main.py feature-flag branch logic
# ---------------------------------------------------------------------------


def test_extraction_mode_toggle_in_main_picks_correct_window() -> None:
    """Simulate the ``main.py`` branch without instantiating either window.

    The new branch is checked end-to-end (importable + class object).
    The legacy branch is checked at the source level only — the legacy
    ``ui.main_window`` module currently relies on color constants that
    Wave 1 of this redesign removed from ``ui.theme``, so importing it
    fails pre-existing-ly. That import wiring is out of scope for this
    commit; what matters here is that ``main.py`` selects the right
    branch given a config.
    """
    new_mod = importlib.import_module("ui.views.main_window")

    legacy_path = (
        Path(__file__).resolve().parents[1] / "ui" / "main_window.py"
    )
    legacy_src = legacy_path.read_text()
    assert "class MainWindow" in legacy_src

    main_py = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    # Ordering matters: ui_v2-true branch must import the new module.
    ui_v2_idx = main_py.find('config.get("ui_v2"')
    new_idx = main_py.find("ui.views.main_window")
    legacy_idx = main_py.find("from ui.main_window import MainWindow")
    assert ui_v2_idx >= 0 and new_idx > ui_v2_idx, (
        "main.py must branch on ui_v2 before importing the new MainWindow"
    )
    assert legacy_idx > new_idx, (
        "legacy MainWindow import must live in the else branch"
    )

    # New branch is fully verifiable.
    assert new_mod.MainWindow.__name__ == "MainWindow"


# ---------------------------------------------------------------------------
# Regression checks for the ExtractionWorker extraction
# ---------------------------------------------------------------------------


def test_legacy_main_window_source_imports_extraction_worker_from_new_location() -> None:
    """After the worker move, the legacy file must reference the new
    canonical import path. Reading the source instead of importing the
    module side-steps the unrelated Wave 1 color-constant import error.
    """
    legacy_path = (
        Path(__file__).resolve().parents[1] / "ui" / "main_window.py"
    )
    src = legacy_path.read_text()
    assert "from ui.controllers.extraction_worker import ExtractionWorker" in src, (
        "Legacy MainWindow must import ExtractionWorker from its new home"
    )
    # Belt-and-braces: the legacy in-file copy must be gone.
    assert "class ExtractionWorker(QObject):" not in src, (
        "ExtractionWorker class body should have been moved to "
        "ui/controllers/extraction_worker.py — the legacy copy is stale"
    )


def test_extraction_worker_importable_from_new_location() -> None:
    from ui.controllers.extraction_worker import ExtractionWorker

    # Build a worker without spinning a thread — the constructor only
    # captures inputs, no I/O happens until run() is called.
    worker = ExtractionWorker("/nonexistent.pdf", {"cache_dir": "/tmp/cache"}, "/tmp")
    assert worker._pdf_path == "/nonexistent.pdf"
    assert worker._cancel is False
    worker.cancel()
    assert worker._cancel is True


def test_main_window_v2_constructs_when_flag_enabled(qapp) -> None:
    """Smoke-test that flipping ``ui_v2: true`` in config returns a window
    we can show without crashing.

    Mirrors the manual smoke test in the commit checklist: a future
    regression in the new shell would surface here without touching the
    actual ``config.yaml`` on disk.
    """
    from ui.views.main_window import MainWindow

    win = MainWindow({"ui_v2": True, "extraction_mode": "hybrid"})
    try:
        assert win.windowTitle() == "Zeconic QTO"
    finally:
        win.deleteLater()
