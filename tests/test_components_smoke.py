"""Smoke tests for the theme + component library (Wave 1)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Force Qt to its offscreen platform so the suite stays headless on CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow


@pytest.fixture(scope="module")
def qapp():
    """Provide a single QApplication instance for the test module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

REQUIRED_COLOR_KEYS = {
    "bg", "border", "text", "accent", "success", "warning", "danger",
    "info", "confirmed-yellow", "revision-pink", "demo-red",
    "approved-green", "mep-blue", "scope-out",
}


def test_tokens_dark_has_all_required_keys() -> None:
    from ui.theme import DARK
    assert REQUIRED_COLOR_KEYS.issubset(DARK["color"].keys())
    assert "1" in DARK["color"]["bg"]["surface"]
    assert "raised" in DARK["color"]["bg"]["surface"]


def test_tokens_light_has_all_required_keys() -> None:
    from ui.theme import LIGHT
    assert REQUIRED_COLOR_KEYS.issubset(LIGHT["color"].keys())
    assert LIGHT["color"]["text"]["primary"] != LIGHT["color"]["bg"]["canvas"]


def test_set_mode_switches_token_proxy() -> None:
    from ui.theme import set_mode, tokens
    set_mode("dark")
    dark_accent = tokens["color"]["accent"]["default"]
    set_mode("light")
    light_accent = tokens["color"]["accent"]["default"]
    set_mode("dark")  # restore default
    assert dark_accent != light_accent


# ---------------------------------------------------------------------------
# QSS generator
# ---------------------------------------------------------------------------

def test_build_stylesheet_returns_non_empty_qss() -> None:
    from ui.theme import build_stylesheet, tokens
    qss = build_stylesheet(tokens)
    assert isinstance(qss, str)
    assert "QPushButton" in qss
    assert "QTableView" in qss
    assert "QLineEdit" in qss


def test_build_stylesheet_uses_token_colors_not_hardcoded() -> None:
    from ui.theme import build_stylesheet, set_mode, tokens
    set_mode("dark")
    qss = build_stylesheet(tokens)
    accent = tokens["color"]["accent"]["default"]
    assert accent in qss
    assert tokens["color"]["bg"]["canvas"] in qss


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def test_load_fonts_handles_missing_files_gracefully(qapp, tmp_path: Path) -> None:
    from ui.theme.fonts import load_fonts
    result = load_fonts(font_dir=tmp_path)  # empty dir, no .ttf files
    assert result["sans_loaded"] is False
    assert result["mono_loaded"] is False
    assert result["errors"], "expected at least one warning"


def test_apply_theme_sets_application_stylesheet(qapp) -> None:
    from ui.theme import apply_theme
    apply_theme(qapp, "dark")
    assert "QPushButton" in qapp.styleSheet()


# ---------------------------------------------------------------------------
# Button
# ---------------------------------------------------------------------------

def test_button_variant_primary_has_correct_property(qapp) -> None:
    from ui.components import Button
    btn = Button("OK", variant="primary", size="md")
    assert btn.property("variant") == "primary"
    assert btn.property("btnSize") == "md"


def test_button_size_lg_height(qapp) -> None:
    from ui.components import Button
    btn = Button("Big", variant="primary", size="lg")
    btn.show()
    QTest.qWait(20)
    assert btn.minimumHeight() >= 44


# ---------------------------------------------------------------------------
# Pill
# ---------------------------------------------------------------------------

def test_pill_with_dot_renders(qapp) -> None:
    from ui.components import Pill
    pill = Pill("Active", variant="success", with_dot=True)
    pill.resize(120, 24)
    pill.show()
    pix = pill.grab()  # forces a paintEvent
    assert pix.width() > 0
    assert pill.property("variant") == "success"


# ---------------------------------------------------------------------------
# StatusPill
# ---------------------------------------------------------------------------

def test_status_pill_high_confidence_shows_confirm(qapp) -> None:
    from ui.components import StatusPill
    sp = StatusPill(confidence=0.95)
    assert "Confirm" in sp.innerPill().text()
    assert sp.actionToken() == "confirm"


def test_status_pill_low_confidence_shows_reextract(qapp) -> None:
    from ui.components import StatusPill
    sp = StatusPill(confidence=0.30)
    assert "Re-extract" in sp.innerPill().text()
    assert sp.actionToken() == "re-extract"


def test_status_pill_emits_action_signal_on_click(qapp) -> None:
    from ui.components import StatusPill
    sp = StatusPill(confidence=0.7)
    sp.resize(160, 28)
    sp.show()
    spy = QSignalSpy(sp.actionRequested)
    QTest.mouseClick(sp, Qt.MouseButton.LeftButton)
    # PyQt6 exposes signal-spy length via len(); the spy lists the args.
    assert len(spy) == 1
    assert spy[0][0] == "review"


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------

def test_card_with_header_renders(qapp) -> None:
    from ui.components import Card
    c = Card(elevation=2, header_text="Stats")
    assert c.header() is not None
    assert c.header().text() == "Stats"
    headers = c.findChildren(QLabel)
    assert any(h.objectName() == "cardHeader" for h in headers)


# ---------------------------------------------------------------------------
# EmptyState
# ---------------------------------------------------------------------------

def test_empty_state_action_button_visible_when_provided(qapp) -> None:
    from ui.components import Button, EmptyState
    btn = Button("Upload", variant="primary", icon_name="upload")
    es = EmptyState(
        icon_name="upload",
        title="No files yet",
        body="Drop a PDF to begin.",
        action_button=btn,
    )
    es.resize(400, 300)
    es.show()
    assert es.actionButton() is btn
    # parent should be set when EmptyState shows
    QTest.qWait(20)


# ---------------------------------------------------------------------------
# Skeleton
# ---------------------------------------------------------------------------

def test_skeleton_shape_line_has_short_height(qapp) -> None:
    from ui.components import Skeleton
    sk = Skeleton(shape="line")
    assert sk.sizeHint().height() <= 24
    assert sk.shape() == "line"


# ---------------------------------------------------------------------------
# Toaster
# ---------------------------------------------------------------------------

def test_toaster_show_appends_toast_to_active_window(qapp) -> None:
    from ui.components import Toast, Toaster
    mw = QMainWindow()
    mw.resize(640, 480)
    mw.show()
    QTest.qWait(20)
    # Pass parent explicitly because activeWindow() is unreliable on offscreen.
    toast = Toaster.show("Saved!", variant="success", duration_ms=10_000, parent=mw)
    assert toast is not None
    assert isinstance(toast, Toast)
    assert toast.parent() is mw
    assert toast in Toaster.visibleToasts()
    Toaster.dismiss(toast)
