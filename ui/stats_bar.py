"""Sidebar stats — compact horizontal metric rows with reliable font rendering."""
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ui.theme import (
    SURFACE_2, SURFACE_3, TEXT_1, TEXT_2, TEXT_3, BORDER_HEX,
    INDIGO, EMERALD, CANVAS, FONT_MONO,
)

def _make_font(family: str, pixel_size: int, bold: bool = False, mono: bool = False) -> QFont:
    f = QFont(family)
    if mono:
        f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPixelSize(pixel_size)
    f.setBold(bold)
    return f

_VALUE_FONT = _make_font(".AppleSystemUIFont", 17, bold=True)
_LABEL_FONT = _make_font(".AppleSystemUIFont", 10, bold=True)
_SUB_FONT   = _make_font("SF Mono", 10, mono=True)


class MetricRow(QFrame):
    """2-row compact metric: label on top, value below."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("metricRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(0)

        inner = QVBoxLayout()
        inner.setSpacing(1)
        inner.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel(title.upper())
        self._label.setFont(_LABEL_FONT)
        self._label.setStyleSheet(f"color: {TEXT_3}; background: transparent;")

        self._value = QLabel("—")
        self._value.setFont(_VALUE_FONT)
        self._value.setStyleSheet(f"color: {TEXT_1}; background: transparent;")

        inner.addWidget(self._label)
        inner.addWidget(self._value)
        layout.addLayout(inner)
        layout.addStretch()

    def set_value(self, value: str):
        self._value.setText(value)


class TokenMetric(QFrame):
    """Tokens metric: label + value + in/out breakdown."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("metricRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(0)

        inner = QVBoxLayout()
        inner.setSpacing(1)
        inner.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel("TOKENS")
        self._label.setFont(_LABEL_FONT)
        self._label.setStyleSheet(f"color: {TEXT_3}; background: transparent;")

        self._value = QLabel("—")
        self._value.setFont(_VALUE_FONT)
        self._value.setStyleSheet(f"color: {TEXT_1}; background: transparent;")

        self._sub = QLabel("")
        self._sub.setFont(_SUB_FONT)
        self._sub.setStyleSheet(f"color: {TEXT_3}; background: transparent;")

        inner.addWidget(self._label)
        inner.addWidget(self._value)
        inner.addWidget(self._sub)
        layout.addLayout(inner)
        layout.addStretch()

    def set_value(self, total: str, breakdown: str = ""):
        self._value.setText(total)
        self._sub.setText(breakdown)


class StatsBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Container card
        card = QFrame()
        card.setObjectName("statCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 8, 0, 8)
        card_layout.setSpacing(0)

        self._pages    = MetricRow("Pages")
        self._rows     = MetricRow("QTO Rows")
        self._api      = MetricRow("API Calls")
        self._tokens   = TokenMetric()
        self._cost     = MetricRow("Est. Cost")
        self._hit_rate = MetricRow("Cache Hit")
        self._by_model = MetricRow("By Model")

        # Thin dividers between rows
        def _divider():
            d = QFrame()
            d.setFrameShape(QFrame.Shape.HLine)
            d.setStyleSheet(f"background: {BORDER_HEX}; max-height: 1px; border: none;")
            d.setMaximumHeight(1)
            return d

        metrics = [self._pages, self._rows, self._api, self._tokens, self._cost, self._hit_rate, self._by_model]
        for i, m in enumerate(metrics):
            card_layout.addWidget(m)
            if i < len(metrics) - 1:
                card_layout.addWidget(_divider())

        layout.addWidget(card)

        # Mode + cache badges
        badge_row = QHBoxLayout()
        badge_row.setSpacing(4)
        badge_row.setContentsMargins(0, 8, 0, 0)

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
        self._pages.set_value(f"{current_page}/{total_pages}")

    def update_rows(self, count: int):
        self._rows.set_value(str(count))

    def update_tokens(self, input_t: int, output_t: int, cache_r: int, cache_w: int, calls: int, cost: float):
        self._api.set_value(str(calls))
        total = input_t + output_t
        total_str = f"{total/1000:.1f}k" if total >= 1000 else str(total)
        breakdown = f"in {input_t/1000:.1f}k  out {output_t/1000:.1f}k"
        self._tokens.set_value(total_str, breakdown)
        self._cost.set_value(f"${cost:.4f}")
        cacheable = cache_r + cache_w
        rate = (cache_r / cacheable * 100) if cacheable else 0.0
        self._hit_rate.set_value(f"{rate:.0f}% ({cache_r/1000:.1f}k)")

    def update_by_model(self, by_model: dict):
        """by_model: {model_id: (calls, cost_usd)}"""
        if not by_model:
            self._by_model.set_value("—")
            return
        # Show a compact tag per family.
        labels = []
        for model, (calls, cost) in by_model.items():
            family = "Hk" if "haiku" in model else "Sn" if "sonnet" in model else "Op" if "opus" in model else model[:6]
            labels.append(f"{family} {calls}·${cost:.3f}")
        self._by_model.set_value("  ".join(labels))

    def set_mode(self, mode: str):
        self._mode_badge.setText(mode.upper())

    def show_cache_hit(self, visible: bool):
        if visible:
            self._cache_badge.show()
        else:
            self._cache_badge.hide()
