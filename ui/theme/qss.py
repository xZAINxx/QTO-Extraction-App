"""QSS stylesheet generator from the design tokens.

The pattern in use:
    * Components set dynamic Qt properties via ``widget.setProperty("variant",
      "primary")`` and the QSS targets ``QPushButton[variant="primary"] { ... }``
      attribute selectors. Adding new variants does NOT require any
      ``polish()``/``unpolish()`` calls in client code as long as the property
      is set BEFORE the widget is shown — Qt resolves attribute selectors at
      style-resolution time.
    * To change a property at runtime (e.g. flip a button from "primary" to
      "danger" mid-flight), call ``widget.style().unpolish(widget); widget
      .style().polish(widget)``. Components in this codebase that mutate a
      style property at runtime do that themselves; consumers should not
      have to.
    * To add a new variant: add the QSS rule below, then set the dynamic
      property on the widget. No new subclass required.

Hardcoded-pixel policy: every measurement comes from the token dict EXCEPT
1px hairline borders, which are taken from ``tokens["border"]["hairline"]``
and emitted as ``{border}px``. Same for 2px focus rings via
``tokens["border"]["thick"]``. The "no hardcoded px" rule polices spacing
and radii — those flow exclusively from ``tokens["space"]`` and
``tokens["radius"]``.
"""
from __future__ import annotations

from typing import Any, Mapping


def _font_face(tokens: Mapping[str, Any]) -> tuple[str, str]:
    sans = tokens["font"]["family"]["sans"]
    mono = tokens["font"]["family"]["mono"]
    sans_stack = f"'{sans}', '-apple-system', 'Segoe UI', sans-serif"
    mono_stack = f"'{mono}', 'SF Mono', 'Monaco', 'Menlo', monospace"
    return sans_stack, mono_stack


def build_stylesheet(tokens: Mapping[str, Any]) -> str:
    """Generate a complete QSS string from the active token set.

    The returned string is meant to be passed straight to
    ``QApplication.setStyleSheet()``. It styles every standard Qt widget the
    QTO tool uses today plus the custom widget classes in
    ``ui/components/``.
    """
    color = tokens["color"]
    space = tokens["space"]
    radius = tokens["radius"]
    border = tokens["border"]
    scale = tokens["font"]["scale"]
    sans, mono = _font_face(tokens)
    bw = border["hairline"]
    bw_thick = border["thick"]

    body = scale["body"]
    body_sm = scale["body-sm"]
    body_lg = scale["body-lg"]
    caption = scale["caption"]

    return f"""
/* === Base ================================================================ */
QMainWindow, QDialog, QWidget {{
    background-color: {color["bg"]["canvas"]};
    color: {color["text"]["primary"]};
    font-family: {sans};
    font-size: {body["size"]}px;
}}

QFrame[surface="1"] {{ background-color: {color["bg"]["surface"]["1"]}; }}
QFrame[surface="2"] {{ background-color: {color["bg"]["surface"]["2"]}; }}
QFrame[surface="3"] {{ background-color: {color["bg"]["surface"]["3"]}; }}

/* === Buttons ============================================================= */
QPushButton {{
    border: {bw}px solid transparent;
    border-radius: {radius["md"]}px;
    padding: {space[2]}px {space[4]}px;
    font-family: {sans};
    font-size: {body["size"]}px;
    font-weight: 600;
}}
QPushButton:focus {{
    border: {bw_thick}px solid {color["accent"]["default"]};
    outline: none;
}}

QPushButton[variant="primary"] {{
    background-color: {color["accent"]["default"]};
    color: {color["accent"]["on"]};
}}
QPushButton[variant="primary"]:hover {{
    background-color: {color["accent"]["hover"]};
}}
QPushButton[variant="primary"]:pressed {{
    background-color: {color["accent"]["pressed"]};
}}
QPushButton[variant="primary"]:disabled {{
    background-color: {color["bg"]["surface"]["3"]};
    color: {color["text"]["tertiary"]};
}}

QPushButton[variant="secondary"] {{
    background-color: {color["bg"]["surface"]["2"]};
    color: {color["text"]["primary"]};
    border: {bw}px solid {color["border"]["default"]};
}}
QPushButton[variant="secondary"]:hover {{
    background-color: {color["bg"]["surface"]["3"]};
    border-color: {color["border"]["strong"]};
}}
QPushButton[variant="secondary"]:pressed {{
    background-color: {color["bg"]["surface"]["1"]};
}}
QPushButton[variant="secondary"]:disabled {{
    color: {color["text"]["tertiary"]};
    border-color: {color["border"]["subtle"]};
}}

QPushButton[variant="ghost"] {{
    background-color: transparent;
    color: {color["text"]["primary"]};
    border: {bw}px solid transparent;
}}
QPushButton[variant="ghost"]:hover {{
    background-color: {color["bg"]["surface"]["2"]};
}}
QPushButton[variant="ghost"]:pressed {{
    background-color: {color["bg"]["surface"]["3"]};
}}
QPushButton[variant="ghost"]:disabled {{
    color: {color["text"]["tertiary"]};
}}

QPushButton[variant="danger"] {{
    background-color: {color["danger"]};
    color: {color["accent"]["on"]};
}}
QPushButton[variant="danger"]:hover {{
    background-color: {color["danger"]};
}}
QPushButton[variant="danger"]:disabled {{
    background-color: {color["bg"]["surface"]["3"]};
    color: {color["text"]["tertiary"]};
}}

QPushButton[btnSize="sm"] {{
    min-height: {space[6] + space[1]}px;
    padding: {space[1]}px {space[3]}px;
    font-size: {body_sm["size"]}px;
}}
QPushButton[btnSize="md"] {{
    min-height: {space[8] + space[1]}px;
    padding: {space[2]}px {space[4]}px;
}}
QPushButton[btnSize="lg"] {{
    min-height: {space[8] + space[3]}px;
    padding: {space[3]}px {space[5]}px;
    font-size: {body_lg["size"]}px;
}}
QPushButton[iconOnly="true"] {{
    padding: {space[2]}px;
    min-width: {space[8] + space[1]}px;
}}

/* === Inputs ============================================================== */
QLineEdit, QPlainTextEdit, QTextEdit {{
    background-color: {color["bg"]["surface"]["2"]};
    color: {color["text"]["primary"]};
    border: {bw}px solid {color["border"]["default"]};
    border-radius: {radius["md"]}px;
    padding: {space[2]}px {space[3]}px;
    selection-background-color: {color["accent"]["default"]};
    selection-color: {color["accent"]["on"]};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {color["accent"]["default"]};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
    color: {color["text"]["tertiary"]};
    background-color: {color["bg"]["surface"]["1"]};
}}

QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {color["bg"]["surface"]["2"]};
    color: {color["text"]["primary"]};
    border: {bw}px solid {color["border"]["default"]};
    border-radius: {radius["md"]}px;
    padding: {space[2]}px {space[3]}px;
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {color["accent"]["default"]};
}}
QComboBox::drop-down {{
    border: none;
    padding-right: {space[2]}px;
}}
QComboBox QAbstractItemView {{
    background-color: {color["bg"]["surface"]["raised"]};
    color: {color["text"]["primary"]};
    border: {bw}px solid {color["border"]["default"]};
    border-radius: {radius["md"]}px;
    padding: {space[1]}px;
    selection-background-color: {color["accent"]["default"]};
    selection-color: {color["accent"]["on"]};
}}

/* === Checkbox + radio ==================================================== */
QCheckBox, QRadioButton {{
    color: {color["text"]["primary"]};
    spacing: {space[2]}px;
    padding: {space[1]}px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: {space[4]}px;
    height: {space[4]}px;
    border: {bw}px solid {color["border"]["strong"]};
    border-radius: {radius["sm"]}px;
    background-color: {color["bg"]["surface"]["2"]};
}}
QRadioButton::indicator {{
    border-radius: {radius["full"]}px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {color["accent"]["default"]};
    border-color: {color["accent"]["default"]};
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {color["accent"]["default"]};
}}

/* === ScrollBars (thin, accent-tinted) ==================================== */
QScrollBar:vertical {{
    background: transparent;
    width: {space[2]}px;
    margin: 0px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {color["border"]["strong"]};
    border-radius: {radius["sm"]}px;
    min-height: {space[6]}px;
}}
QScrollBar::handle:vertical:hover {{
    background: {color["accent"]["default"]};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: {space[2]}px;
    margin: 0px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {color["border"]["strong"]};
    border-radius: {radius["sm"]}px;
    min-width: {space[6]}px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {color["accent"]["default"]};
}}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    height: 0px;
    width: 0px;
}}

/* === Tables ============================================================== */
QTableView {{
    background-color: {color["bg"]["surface"]["1"]};
    alternate-background-color: {color["bg"]["surface"]["2"]};
    color: {color["text"]["primary"]};
    gridline-color: {color["border"]["subtle"]};
    border: {bw}px solid {color["border"]["subtle"]};
    border-radius: {radius["lg"]}px;
    selection-background-color: {color["accent"]["subtle"]};
    selection-color: {color["text"]["primary"]};
    font-size: {body_sm["size"]}px;
}}
QTableView::item {{
    padding: {space[1]}px {space[2]}px;
}}
QTableView::item:hover {{
    background-color: {color["bg"]["surface"]["3"]};
}}
QHeaderView::section {{
    background-color: {color["bg"]["surface"]["2"]};
    color: {color["text"]["secondary"]};
    font-weight: 600;
    font-size: {caption["size"]}px;
    padding: {space[2]}px {space[3]}px;
    border: none;
    border-bottom: {bw}px solid {color["border"]["default"]};
}}

/* === Tabs ================================================================ */
QTabWidget::pane {{
    border: {bw}px solid {color["border"]["subtle"]};
    border-radius: {radius["lg"]}px;
    background-color: {color["bg"]["surface"]["1"]};
    top: -{bw}px;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {color["text"]["secondary"]};
    padding: {space[2]}px {space[4]}px;
    border: none;
    border-bottom: {bw_thick}px solid transparent;
    font-size: {body["size"]}px;
}}
QTabBar::tab:hover {{
    color: {color["text"]["primary"]};
}}
QTabBar::tab:selected {{
    color: {color["accent"]["default"]};
    border-bottom: {bw_thick}px solid {color["accent"]["default"]};
}}

/* === Menu / MenuBar ====================================================== */
QMenuBar {{
    background-color: {color["bg"]["surface"]["1"]};
    color: {color["text"]["primary"]};
    border-bottom: {bw}px solid {color["border"]["subtle"]};
}}
QMenuBar::item {{
    background: transparent;
    padding: {space[1]}px {space[3]}px;
}}
QMenuBar::item:selected {{
    background-color: {color["bg"]["surface"]["2"]};
}}
QMenu {{
    background-color: {color["bg"]["surface"]["raised"]};
    color: {color["text"]["primary"]};
    border: {bw}px solid {color["border"]["default"]};
    border-radius: {radius["lg"]}px;
    padding: {space[1]}px;
}}
QMenu::item {{
    padding: {space[1]}px {space[4]}px;
    border-radius: {radius["sm"]}px;
}}
QMenu::item:selected {{
    background-color: {color["accent"]["default"]};
    color: {color["accent"]["on"]};
}}
QMenu::separator {{
    height: {bw}px;
    background-color: {color["border"]["subtle"]};
    margin: {space[1]}px {space[2]}px;
}}

/* === Tooltip ============================================================= */
QToolTip {{
    background-color: {color["bg"]["surface"]["raised"]};
    color: {color["text"]["primary"]};
    border: {bw}px solid {color["border"]["default"]};
    border-radius: {radius["sm"]}px;
    padding: {space[1]}px {space[2]}px;
    font-size: {body_sm["size"]}px;
}}

/* === Dock + Splitter ===================================================== */
QDockWidget {{
    color: {color["text"]["primary"]};
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}}
QDockWidget::title {{
    background-color: {color["bg"]["surface"]["2"]};
    color: {color["text"]["secondary"]};
    padding: {space[2]}px {space[3]}px;
    border-bottom: {bw}px solid {color["border"]["subtle"]};
    font-weight: 600;
    font-size: {body_sm["size"]}px;
}}
QSplitter::handle {{
    background-color: {color["border"]["subtle"]};
}}
QSplitter::handle:horizontal {{
    width: {bw}px;
}}
QSplitter::handle:vertical {{
    height: {bw}px;
}}
QSplitter::handle:hover {{
    background-color: {color["accent"]["default"]};
}}

/* === Progress Bar (thin track, accent fill) ============================== */
QProgressBar {{
    background-color: {color["bg"]["surface"]["2"]};
    border: none;
    border-radius: {radius["sm"]}px;
    height: {space[1]}px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {color["accent"]["default"]};
    border-radius: {radius["sm"]}px;
}}

/* === Cards =============================================================== */
QFrame#card {{
    background-color: {color["bg"]["surface"]["1"]};
    border: {bw}px solid {color["border"]["subtle"]};
    border-radius: {radius["lg"]}px;
}}
QFrame#card[elevation="0"] {{
    background-color: {color["bg"]["surface"]["1"]};
}}
QFrame#card[elevation="2"] {{
    background-color: {color["bg"]["surface"]["raised"]};
}}
QLabel#cardHeader {{
    color: {color["text"]["primary"]};
    font-size: {body_lg["size"]}px;
    font-weight: 600;
    padding: {space[3]}px {space[4]}px;
    border-bottom: {bw}px solid {color["border"]["subtle"]};
    background: transparent;
}}

/* === Typography helpers ================================================== */
QLabel[textSize="caption"] {{
    color: {color["text"]["tertiary"]};
    font-size: {caption["size"]}px;
    font-weight: {caption["weight"]};
}}
QLabel[textSize="body-sm"] {{ font-size: {body_sm["size"]}px; }}
QLabel[textSize="body"] {{ font-size: {body["size"]}px; }}
QLabel[textSize="h6"] {{ font-size: {scale["h6"]["size"]}px; font-weight: 600; }}
QLabel[textSize="h5"] {{ font-size: {scale["h5"]["size"]}px; font-weight: 600; }}
QLabel[textSize="h4"] {{ font-size: {scale["h4"]["size"]}px; font-weight: 600; }}
QLabel[textSize="h3"] {{ font-size: {scale["h3"]["size"]}px; font-weight: 600; }}
QLabel[textSize="h2"] {{ font-size: {scale["h2"]["size"]}px; font-weight: 600; }}
QLabel[textSize="h1"] {{ font-size: {scale["h1"]["size"]}px; font-weight: 600; }}

/* === Pills =============================================================== */
QLabel#pill {{
    border-radius: {radius["full"]}px;
    padding: {space[1]}px {space[2]}px;
    font-size: {body_sm["size"]}px;
    font-weight: 500;
}}
QLabel#pill[variant="info"] {{
    background-color: {color["bg"]["surface"]["3"]};
    color: {color["info"]};
}}
QLabel#pill[variant="success"] {{
    background-color: {color["accent"]["subtle"]};
    color: {color["success"]};
}}
QLabel#pill[variant="warning"] {{
    background-color: {color["bg"]["surface"]["2"]};
    color: {color["warning"]};
}}
QLabel#pill[variant="danger"] {{
    background-color: {color["bg"]["surface"]["2"]};
    color: {color["danger"]};
}}
QLabel#pill[variant="neutral"] {{
    background-color: {color["bg"]["surface"]["2"]};
    color: {color["text"]["secondary"]};
}}
"""


__all__ = ["build_stylesheet"]
