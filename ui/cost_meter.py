"""Compact, pinned cost meter — extracted from ``StatsBar``.

Lives in the splitter footer (or any pinned slot). Subscribes to the same
``ExtractionWorker`` signals as ``StatsBar`` so both stay in sync.

Layout (single row, monospace):
    $0.0184  •  4.2k tok  •  92% hit  •  Hk 12·$0.003  Sn 4·$0.015
"""
from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ui.theme import (
    SURFACE_2, SURFACE_3, TEXT_1, TEXT_2, TEXT_3, BORDER_HEX,
    EMERALD, INDIGO,
)


def _mono(size: int = 11, bold: bool = False) -> QFont:
    f = QFont("SF Mono")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPixelSize(size)
    f.setBold(bold)
    return f


def _ui(size: int = 11, bold: bool = False) -> QFont:
    f = QFont(".AppleSystemUIFont")
    f.setPixelSize(size)
    f.setBold(bold)
    return f


class CostMeter(QFrame):
    """Pinned, single-row cost & token meter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("costMeter")
        self.setStyleSheet(
            f"#costMeter {{"
            f"  background: {SURFACE_2};"
            f"  border-top: 1px solid {BORDER_HEX};"
            f"}}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(14)

        self._cost = QLabel("$0.0000")
        self._cost.setFont(_mono(13, bold=True))
        self._cost.setStyleSheet(f"color: {EMERALD};")
        layout.addWidget(self._cost)

        layout.addWidget(self._dot())

        self._tokens = QLabel("0 tok")
        self._tokens.setFont(_mono(11))
        self._tokens.setStyleSheet(f"color: {TEXT_2};")
        layout.addWidget(self._tokens)

        layout.addWidget(self._dot())

        self._hit = QLabel("0% hit")
        self._hit.setFont(_mono(11))
        self._hit.setStyleSheet(f"color: {TEXT_2};")
        layout.addWidget(self._hit)

        layout.addWidget(self._dot())

        self._by_model = QLabel("")
        self._by_model.setFont(_mono(11))
        self._by_model.setStyleSheet(f"color: {TEXT_3};")
        layout.addWidget(self._by_model)

        layout.addStretch()

        self._mode = QLabel("HYBRID")
        self._mode.setFont(_ui(10, bold=True))
        self._mode.setStyleSheet(
            f"color: {INDIGO}; padding: 2px 8px; border: 1px solid {INDIGO}; "
            f"border-radius: 6px; background: transparent;"
        )
        layout.addWidget(self._mode)

    def _dot(self) -> QLabel:
        d = QLabel("•")
        d.setStyleSheet(f"color: {TEXT_3};")
        d.setFont(_mono(11))
        return d

    def update_tokens(
        self,
        input_t: int,
        output_t: int,
        cache_r: int,
        cache_w: int,
        calls: int,
        cost: float,
    ):
        total = input_t + output_t
        total_str = f"{total/1000:.1f}k" if total >= 1000 else f"{total}"
        self._tokens.setText(f"{total_str} tok")
        self._cost.setText(f"${cost:.4f}")
        cacheable = cache_r + cache_w
        rate = (cache_r / cacheable * 100) if cacheable else 0.0
        self._hit.setText(f"{rate:.0f}% hit")

    def update_by_model(self, by_model: dict):
        """``{model_id: (calls, cost_usd)}``."""
        if not by_model:
            self._by_model.setText("")
            return
        chunks = []
        for model, (calls, cost) in by_model.items():
            family = (
                "Hk" if "haiku" in model
                else "Sn" if "sonnet" in model
                else "Op" if "opus" in model
                else "Nm" if "nemotron-mini" in model
                else "Mn" if "mistral-nemotron" in model
                else "Mv" if "maverick" in model
                else "Em" if "nv-embed" in model
                else "Rr" if "rerank" in model
                else model[:4]
            )
            chunks.append(f"{family} {calls}·${cost:.3f}")
        self._by_model.setText("  ".join(chunks))

    def set_mode(self, mode: str):
        self._mode.setText(mode.upper())

    def reset(self):
        self._cost.setText("$0.0000")
        self._tokens.setText("0 tok")
        self._hit.setText("0% hit")
        self._by_model.setText("")
