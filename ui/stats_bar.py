"""Sidebar stats cards — pages, rows, API calls, tokens, cost, mode."""
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QHBoxLayout, QWidget
from PyQt6.QtCore import Qt

from ui.theme import (
    SURFACE_2, TEXT_1, TEXT_2, TEXT_3, BORDER_HEX, INDIGO, EMERALD, CANVAS
)


class StatCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        self._title = QLabel(title.upper())
        self._title.setObjectName("cardTitle")
        self._value = QLabel("—")
        self._value.setObjectName("cardValue")
        self._sub = QLabel("")
        self._sub.setObjectName("cardSub")

        layout.addWidget(self._title)
        layout.addWidget(self._value)
        layout.addWidget(self._sub)

    def update(self, value: str, sub: str = ""):
        self._value.setText(value)
        self._sub.setText(sub)


class StatsBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._pages = StatCard("Pages")
        self._rows = StatCard("QTO Rows")
        self._api = StatCard("API Calls")
        self._tokens = StatCard("Tokens")
        self._cost = StatCard("Est. Cost")

        for card in (self._pages, self._rows, self._api, self._tokens, self._cost):
            layout.addWidget(card)

        # Mode + cache badges
        badge_row = QHBoxLayout()
        badge_row.setSpacing(4)
        self._mode_badge = QLabel("HYBRID")
        self._mode_badge.setObjectName("badgeMode")
        self._mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cache_badge = QLabel("CACHE HIT")
        self._cache_badge.setObjectName("badgeCache")
        self._cache_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cache_badge.hide()

        badge_row.addWidget(self._mode_badge)
        badge_row.addWidget(self._cache_badge)
        badge_row.addStretch()
        layout.addLayout(badge_row)
        layout.addStretch()

    def update_progress(self, current_page: int, total_pages: int):
        self._pages.update(f"{current_page}/{total_pages}", "processed")

    def update_rows(self, count: int):
        self._rows.update(str(count), "extracted")

    def update_tokens(self, input_t: int, output_t: int, cache_r: int, cache_w: int, calls: int, cost: float):
        self._api.update(str(calls), "calls")
        self._tokens.update(
            f"{(input_t + output_t):,}",
            f"In:{input_t:,} Out:{output_t:,}\nCR:{cache_r:,} CW:{cache_w:,}",
        )
        self._cost.update(f"${cost:.4f}")

    def set_mode(self, mode: str):
        self._mode_badge.setText(mode.upper())

    def show_cache_hit(self, visible: bool):
        if visible:
            self._cache_badge.show()
        else:
            self._cache_badge.hide()
