"""Wave 5 commit 7 — DiffWorkspace tests.

The DiffWorkspace promotes the legacy ``SetDiffDialog`` modal into a
first-class workspace tab and adds a $-impact column derived from the
currently extracted ``QTORow`` set. We follow the same headless /
module-scoped ``QApplication`` pattern as ``test_layout_shell.py`` and
``test_components_smoke.py`` — no pytest-qt dependency, just an
offscreen Qt platform plugin. Heavy I/O (PDF rendering, OpenCV
homography) is patched so tests stay fast.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (
    QApplication,
    QListWidget,
    QPlainTextEdit,
    QSplitter,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_diff_result(*, modified_pages: list[tuple[str, int]]):
    """Build a fake SetDiffResult with ``modified`` pages on given new_page numbers."""
    from core.set_diff import DiffCluster, PageDiff, SetDiffResult

    import fitz

    pairs = []
    for sheet_id, new_page in modified_pages:
        pairs.append(
            PageDiff(
                sheet_id=sheet_id,
                status="modified",
                old_page=new_page,
                new_page=new_page,
                clusters=[
                    DiffCluster(pdf_rect=fitz.Rect(0, 0, 100, 100), pixel_count=400),
                    DiffCluster(pdf_rect=fitz.Rect(50, 50, 150, 150), pixel_count=200),
                ],
            )
        )
    return SetDiffResult(old_pdf="/old.pdf", new_pdf="/new.pdf", pairs=pairs)


def _row(source_page: int, qty: float, unit_price: float):
    from core.qto_row import QTORow

    return QTORow(
        s_no=1, tag="T", description="d", qty=qty, units="LF",
        unit_price=unit_price, source_page=source_page,
    )


# ---------------------------------------------------------------------------
# Construction & layout
# ---------------------------------------------------------------------------


def test_diff_workspace_construction(qapp) -> None:
    from ui.workspaces.diff_workspace import DiffWorkspace

    ws = DiffWorkspace(config={})
    try:
        assert ws.sizeHint().isValid()
        assert ws.is_running() is False
    finally:
        ws.deleteLater()


def test_diff_workspace_top_bar_has_file_labels_and_rerun_button(qapp) -> None:
    from ui.components import Button
    from ui.workspaces.diff_workspace import DiffWorkspace

    ws = DiffWorkspace(config={})
    try:
        # File labels live under stable object names so callers can verify them.
        old_label = ws.findChild(object, "diffOldFileLabel")
        new_label = ws.findChild(object, "diffNewFileLabel")
        assert old_label is not None and new_label is not None
        rerun_btn = ws.findChild(Button, "diffRerunBtn")
        assert rerun_btn is not None
        # Splitter + sheet list + summary widget present.
        assert ws.findChild(QSplitter, "diffSplitter") is not None
        assert ws.findChild(QListWidget, "diffSheetList") is not None
        assert ws.findChild(QPlainTextEdit, "diffSummary") is not None
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Existing-rows caching + dollar impact math
# ---------------------------------------------------------------------------


def test_diff_workspace_set_existing_rows_caches_rows(qapp) -> None:
    from ui.workspaces.diff_workspace import DiffWorkspace

    ws = DiffWorkspace(config={})
    try:
        rows = [_row(1, 10, 5.0), _row(2, 4, 12.5)]
        ws.set_existing_rows(rows)
        assert list(ws._existing_rows) == rows
    finally:
        ws.deleteLater()


def test_diff_workspace_dollar_impact_calculation(qapp) -> None:
    """Three rows on three pages → impact equals qty*unit_price per page."""
    from ui.workspaces.diff_workspace import DiffWorkspace

    ws = DiffWorkspace(config={})
    try:
        rows = [
            _row(1, 10, 5.0),    # page 1: $50
            _row(2, 4, 12.5),    # page 2: $50
            _row(3, 100, 0.75),  # page 3: $75
            _row(2, 2, 10.0),    # page 2: +$20 → page 2 total $70
        ]
        ws.set_existing_rows(rows)
        result = _make_diff_result(modified_pages=[
            ("A-101", 1), ("A-102", 2), ("A-103", 3),
        ])
        formatted = [ws._format_sheet_label(p) for p in result.pairs]
        # Spot-check each page's $ amount lands in the formatted string.
        assert "A-101" in formatted[0] and "$50" in formatted[0]
        assert "A-102" in formatted[1] and "$70" in formatted[1]
        assert "A-103" in formatted[2] and "$75" in formatted[2]
        # Cluster count is also surfaced so reviewers see the change density.
        assert "2 changes" in formatted[0]
    finally:
        ws.deleteLater()


def test_diff_workspace_handles_zero_unit_price_gracefully(qapp) -> None:
    """qty=10 + unit_price=0 → impact $0 with no division/format errors."""
    from ui.workspaces.diff_workspace import DiffWorkspace

    ws = DiffWorkspace(config={})
    try:
        ws.set_existing_rows([_row(1, 10, 0.0), _row(1, 5, 0.0)])
        result = _make_diff_result(modified_pages=[("A-101", 1)])
        label = ws._format_sheet_label(result.pairs[0])
        assert "$0" in label
        # No NaN / "inf" / exception leaking through.
        assert "nan" not in label.lower()
        assert "inf" not in label.lower()
    finally:
        ws.deleteLater()


def test_diff_workspace_label_degrades_when_no_rows_available(qapp) -> None:
    """If set_existing_rows was never called, just show change count."""
    from ui.workspaces.diff_workspace import DiffWorkspace

    ws = DiffWorkspace(config={})
    try:
        result = _make_diff_result(modified_pages=[("A-101", 1)])
        label = ws._format_sheet_label(result.pairs[0])
        assert "A-101" in label
        assert "2 changes" in label
        # Without rows we never claim a $ figure.
        assert "$" not in label
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_diff_workspace_clear_resets_state(qapp) -> None:
    from ui.workspaces.diff_workspace import DiffWorkspace

    ws = DiffWorkspace(config={})
    try:
        ws.set_existing_rows([_row(1, 1, 1.0)])
        ws._sheet_list.addItem("stale row")
        ws._summary.setPlainText("stale summary")
        ws._old_label.setText("Old: stale.pdf")
        ws._new_label.setText("New: stale.pdf")

        ws.clear()
        assert ws._sheet_list.count() == 0
        assert ws._summary.toPlainText() == ""
        assert ws._result is None
        # File labels reset to the default "no file loaded" state.
        assert "—" in ws._old_label.text() or "Old:" in ws._old_label.text()
    finally:
        ws.deleteLater()


def test_diff_workspace_rerun_signal_emits_set_diff_result(qapp) -> None:
    from core.set_diff import SetDiffResult
    from ui.workspaces.diff_workspace import DiffWorkspace

    ws = DiffWorkspace(config={})
    captured: list[SetDiffResult] = []
    ws.rerun_requested.connect(lambda r: captured.append(r))
    try:
        fake_result = _make_diff_result(modified_pages=[("A-1", 1)])
        ws._result = fake_result
        ws._rerun_btn.setEnabled(True)
        # Patch QMessageBox so the test doesn't open a dialog.
        with patch(
            "ui.workspaces.diff_workspace.QMessageBox.question",
            return_value=ws._YES,
        ):
            ws._on_rerun_clicked()
        assert len(captured) == 1
        assert captured[0] is fake_result
    finally:
        ws.deleteLater()


def test_diff_workspace_open_compare_starts_worker_thread(qapp, tmp_path) -> None:
    """Smoke: open_compare wires a _DiffWorker and starts a thread."""
    from ui.workspaces.diff_workspace import DiffWorkspace

    ws = DiffWorkspace(config={})
    try:
        # Patch the worker so no real diff_sets call fires.
        worker_mock = MagicMock()
        worker_mock.progress = MagicMock()
        worker_mock.finished = MagicMock()
        worker_mock.error = MagicMock()
        with patch(
            "ui.workspaces.diff_workspace._DiffWorker",
            return_value=worker_mock,
        ) as worker_ctor, patch(
            "ui.workspaces.diff_workspace.QThread"
        ) as thread_ctor:
            thread_inst = MagicMock()
            thread_inst.isRunning.return_value = True
            thread_ctor.return_value = thread_inst
            ws.open_compare("/old.pdf", "/new.pdf", ai_client=None)
            worker_ctor.assert_called_once()
            thread_inst.start.assert_called_once()
            assert ws.is_running() is True
            # File labels updated to reflect the inputs.
            assert "old.pdf" in ws._old_label.text()
            assert "new.pdf" in ws._new_label.text()
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Integration with MainWindow
# ---------------------------------------------------------------------------


def test_main_window_workspace_host_includes_real_diff_workspace(qapp) -> None:
    """The "Diff" placeholder is replaced by a live DiffWorkspace tab."""
    from PyQt6.QtWidgets import QTabWidget

    from ui.views.main_window import MainWindow
    from ui.workspaces.diff_workspace import DiffWorkspace

    win = MainWindow({})
    try:
        host = win.findChild(QTabWidget, "workspaceHost")
        assert host is not None
        labels = [host.tabText(i) for i in range(host.count())]
        # The tab label moves from "(coming soon)" to a real one.
        assert any(lbl == "What Changed" for lbl in labels), labels
        # The widget at that index is a DiffWorkspace, not an EmptyState.
        diff_idx = next(i for i, lbl in enumerate(labels) if lbl == "What Changed")
        assert isinstance(host.widget(diff_idx), DiffWorkspace)
        assert host.isTabEnabled(diff_idx)
    finally:
        win.deleteLater()


def test_main_window_compare_button_present_in_topbar(qapp) -> None:
    from ui.components import Button
    from ui.views.main_window import MainWindow

    win = MainWindow({})
    try:
        btn = win.findChild(Button, "compareBtn")
        assert btn is not None
    finally:
        win.deleteLater()


def test_main_window_compare_button_warns_when_no_pdf_loaded(qapp) -> None:
    from ui.views.main_window import MainWindow

    win = MainWindow({})
    try:
        with patch("ui.views.main_window.Toaster.show") as toast:
            win._on_compare_with()
        toast.assert_called_once()
        message = toast.call_args[0][0]
        assert "Load a PDF" in message
    finally:
        win.deleteLater()
