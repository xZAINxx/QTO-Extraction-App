"""Color tokens and stylesheet derived from Zeconic CPM app design system."""

# Canvas / Surface
CANVAS = "#060816"
SURFACE_1 = "#0D1326"
SURFACE_2 = "#111A31"
SURFACE_3 = "#17213E"
SURFACE_4 = "#1E2A4C"

# Accents
INDIGO = "#3B82F6"
EMERALD = "#10B981"
AMBER = "#F59E0B"
RED = "#EF4444"
PURPLE = "#8B5CF6"

# Text
TEXT_1 = "#F1F5F9"
TEXT_2 = "#94A3B8"
TEXT_3 = "#64748B"

# Borders
BORDER_1 = "rgba(255,255,255,0.08)"
BORDER_HEX = "#1E2A4C"

# Section header bg (subtle)
SECTION_BG = "#17213E"

# Sidebar width
SIDEBAR_WIDTH = 220


STYLESHEET = f"""
QMainWindow, QDialog {{
    background-color: {CANVAS};
    color: {TEXT_1};
    font-family: 'Plus Jakarta Sans', 'Segoe UI', sans-serif;
    font-size: 13px;
}}
QWidget {{
    background-color: {CANVAS};
    color: {TEXT_1};
}}

/* Sidebar */
QFrame#sidebar {{
    background-color: {SURFACE_1};
    border-right: 1px solid {BORDER_HEX};
}}

/* Cards */
QFrame#statCard {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER_HEX};
    border-radius: 8px;
    padding: 6px;
}}
QLabel#cardTitle {{
    color: {TEXT_3};
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}
QLabel#cardValue {{
    color: {TEXT_1};
    font-size: 18px;
    font-weight: 700;
}}
QLabel#cardSub {{
    color: {TEXT_2};
    font-size: 11px;
}}

/* Buttons */
QPushButton {{
    background-color: {INDIGO};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: #4F96FF;
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
    background-color: {AMBER};
    color: {CANVAS};
    font-size: 11px;
    padding: 4px 8px;
}}

/* Table */
QTableWidget {{
    background-color: {SURFACE_1};
    alternate-background-color: {SURFACE_2};
    gridline-color: {BORDER_HEX};
    border: 1px solid {BORDER_HEX};
    border-radius: 6px;
    color: {TEXT_1};
    selection-background-color: {INDIGO};
    font-size: 12px;
}}
QHeaderView::section {{
    background-color: {SURFACE_3};
    color: {TEXT_2};
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid {BORDER_HEX};
    border-right: 1px solid {BORDER_HEX};
}}
QTableWidget::item:selected {{
    background-color: {INDIGO};
    color: white;
}}
QTableWidget::item:hover {{
    background-color: {SURFACE_3};
}}

/* Input fields */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {SURFACE_2};
    color: {TEXT_1};
    border: 1px solid {BORDER_HEX};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {INDIGO};
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
}}

/* Progress bar */
QProgressBar {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER_HEX};
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {INDIGO};
    border-radius: 4px;
}}

/* ScrollBar */
QScrollBar:vertical {{
    background: {SURFACE_1};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {SURFACE_4};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* Label variations */
QLabel {{
    color: {TEXT_1};
}}
QLabel#muted {{
    color: {TEXT_2};
    font-size: 12px;
}}
QLabel#sectionLabel {{
    color: {TEXT_3};
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}}

/* Badge */
QLabel#badge {{
    background-color: {SURFACE_3};
    color: {TEXT_2};
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#badgeMode {{
    background-color: {INDIGO};
    color: white;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#badgeCache {{
    background-color: {EMERALD};
    color: {CANVAS};
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 700;
}}
"""
