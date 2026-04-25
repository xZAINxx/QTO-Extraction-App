"""Design system — Zeconic QTO Tool."""

# ── Canvas / Surface ──────────────────────────────────────────────────────────
CANVAS    = "#08091A"
SURFACE_1 = "#0E1528"
SURFACE_2 = "#141E35"
SURFACE_3 = "#1A2542"
SURFACE_4 = "#202E52"

# ── Accents ───────────────────────────────────────────────────────────────────
INDIGO  = "#3D8EF0"   # Electric blue — less generic than stock #3B82F6
EMERALD = "#10B981"
AMBER   = "#F59E0B"
RED     = "#EF4444"
PURPLE  = "#8B5CF6"

# ── Text ──────────────────────────────────────────────────────────────────────
TEXT_1 = "#EFF2F7"
TEXT_2 = "#8E9DC0"
TEXT_3 = "#556080"

# ── Borders ───────────────────────────────────────────────────────────────────
BORDER_1   = "rgba(255,255,255,0.06)"
BORDER_HEX = "#1C2848"

# ── Section background ────────────────────────────────────────────────────────
SECTION_BG = "#182038"

# ── Sidebar ───────────────────────────────────────────────────────────────────
SIDEBAR_WIDTH = 248

# ── Font stack (macOS system fonts — always available) ───────────────────────
FONT_BODY = "'.AppleSystemUIFont', 'Helvetica Neue', 'Arial', sans-serif"
FONT_MONO = "'SF Mono', 'Monaco', 'Menlo', 'Courier New', monospace"


STYLESHEET = f"""
/* ── Base ──────────────────────────────────────────────────────────────────── */
QMainWindow, QDialog {{
    background-color: {CANVAS};
    color: {TEXT_1};
    font-family: {FONT_BODY};
    font-size: 13px;
}}
QWidget {{
    background-color: {CANVAS};
    color: {TEXT_1};
    font-family: {FONT_BODY};
}}

/* ── Sidebar ────────────────────────────────────────────────────────────────── */
QFrame#sidebar {{
    background-color: {SURFACE_1};
    border-right: 1px solid {BORDER_HEX};
}}

/* ── Metric chips (horizontal label + value rows) ───────────────────────────── */
QFrame#metricRow {{
    background: transparent;
    border: none;
}}

/* ── Stat cards ─────────────────────────────────────────────────────────────── */
QFrame#statCard {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER_HEX};
    border-radius: 10px;
}}
QLabel#cardTitle {{
    color: {TEXT_3};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    background: transparent;
}}
QLabel#cardValue {{
    color: {TEXT_1};
    font-size: 22px;
    font-weight: 700;
    background: transparent;
    margin: 0px;
    padding: 0px;
}}
QLabel#cardSub {{
    color: {TEXT_3};
    font-size: 10px;
    background: transparent;
    font-family: {FONT_MONO};
}}

/* ── Buttons ────────────────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {INDIGO};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 9px 16px;
    font-weight: 600;
    font-size: 13px;
    font-family: {FONT_BODY};
}}
QPushButton:hover {{
    background-color: #5BA0F5;
}}
QPushButton:pressed {{
    background-color: #2563EB;
}}
QPushButton:disabled {{
    background-color: {SURFACE_3};
    color: {TEXT_3};
}}
QPushButton#cancelBtn {{
    background-color: {RED};
}}
QPushButton#cancelBtn:hover {{
    background-color: #F87171;
}}
QPushButton#exportBtn {{
    background-color: {EMERALD};
}}
QPushButton#exportBtn:hover {{
    background-color: #34D399;
}}
QPushButton#retryBtn {{
    background-color: transparent;
    color: {AMBER};
    border: 1px solid {AMBER};
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 5px;
}}
QPushButton#retryBtn:hover {{
    background-color: {AMBER};
    color: {CANVAS};
}}

/* ── Table ──────────────────────────────────────────────────────────────────── */
QTableWidget {{
    background-color: {SURFACE_1};
    alternate-background-color: {SURFACE_2};
    gridline-color: {BORDER_HEX};
    border: 1px solid {BORDER_HEX};
    border-radius: 8px;
    color: {TEXT_1};
    selection-background-color: {INDIGO};
    font-size: 12px;
    font-family: {FONT_BODY};
}}
QHeaderView::section {{
    background-color: {SURFACE_3};
    color: {TEXT_3};
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.07em;
    padding: 7px 8px;
    border: none;
    border-bottom: 1px solid {BORDER_HEX};
    border-right: 1px solid {BORDER_HEX};
    font-family: {FONT_BODY};
}}
QTableWidget::item:selected {{
    background-color: {INDIGO};
    color: white;
}}
QTableWidget::item:hover {{
    background-color: {SURFACE_3};
}}

/* ── Input fields ───────────────────────────────────────────────────────────── */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {SURFACE_2};
    color: {TEXT_1};
    border: 1px solid {BORDER_HEX};
    border-radius: 7px;
    padding: 6px 10px;
    font-size: 13px;
    font-family: {FONT_BODY};
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {INDIGO};
    background-color: {SURFACE_3};
}}
QLineEdit::placeholder, QLineEdit[text=""] {{
    color: {TEXT_3};
}}
QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE_2};
    color: {TEXT_1};
    selection-background-color: {INDIGO};
    border: 1px solid {BORDER_HEX};
    border-radius: 6px;
    padding: 4px;
}}

/* ── Progress bar ───────────────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {SURFACE_3};
    border: none;
    border-radius: 3px;
    height: 4px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {INDIGO};
    border-radius: 3px;
}}

/* ── ScrollBar ──────────────────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {SURFACE_1};
    width: 6px;
    border-radius: 3px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {SURFACE_4};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: {SURFACE_1};
    height: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: {SURFACE_4};
    border-radius: 3px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ── Label variations ───────────────────────────────────────────────────────── */
QLabel {{
    color: {TEXT_1};
    font-family: {FONT_BODY};
}}
QLabel#muted {{
    color: {TEXT_2};
    font-size: 12px;
    background: transparent;
}}
QLabel#sectionLabel {{
    color: {TEXT_3};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    background: transparent;
}}
QLabel#emptyState {{
    color: {TEXT_3};
    font-size: 13px;
    background: transparent;
}}
QLabel#emptyStateTitle {{
    color: {TEXT_2};
    font-size: 15px;
    font-weight: 600;
    background: transparent;
}}

/* ── Badges ─────────────────────────────────────────────────────────────────── */
QLabel#badgeMode {{
    background-color: rgba(61, 142, 240, 0.15);
    color: {INDIGO};
    border: 1px solid rgba(61, 142, 240, 0.3);
    border-radius: 5px;
    padding: 2px 8px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.06em;
}}
QLabel#badgeCache {{
    background-color: rgba(16, 185, 129, 0.15);
    color: {EMERALD};
    border: 1px solid rgba(16, 185, 129, 0.3);
    border-radius: 5px;
    padding: 2px 8px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.06em;
}}

/* ── Dividers ───────────────────────────────────────────────────────────────── */
QFrame[frameShape="4"] {{
    color: {BORDER_HEX};
    background: {BORDER_HEX};
    max-height: 1px;
    border: none;
}}

/* ── Context menu ───────────────────────────────────────────────────────────── */
QMenu {{
    background: {SURFACE_2};
    color: {TEXT_1};
    border: 1px solid {BORDER_HEX};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 16px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: {INDIGO};
    color: white;
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER_HEX};
    margin: 3px 8px;
}}

/* ── Tooltip ────────────────────────────────────────────────────────────────── */
QToolTip {{
    background: {SURFACE_3};
    color: {TEXT_1};
    border: 1px solid {BORDER_HEX};
    border-radius: 5px;
    padding: 4px 8px;
    font-size: 12px;
}}
"""
