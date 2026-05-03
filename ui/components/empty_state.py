"""EmptyState — centered icon + title + body + optional CTA."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.components.button import Button
from ui.theme import icon as theme_icon, tokens


class EmptyState(QWidget):
    """Friendly placeholder shown when a list / table / canvas is empty."""

    def __init__(
        self,
        icon_name: str = "upload",
        title: str = "",
        body: str = "",
        action_button: Button | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._action_button: Button | None = action_button

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            tokens["space"][6],
            tokens["space"][6],
            tokens["space"][6],
            tokens["space"][6],
        )
        outer.setSpacing(tokens["space"][3])
        outer.addStretch(1)

        # --- icon -----------------------------------------------------------
        self._icon_label = QLabel(self)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setFixedSize(QSize(48, 48))
        try:
            qicon = theme_icon(icon_name, color=tokens["color"]["text"]["secondary"], size=48)
            self._icon_label.setPixmap(qicon.pixmap(48, 48))
        except RuntimeError:
            self._icon_label.setText("•")  # qtawesome missing — minimal fallback
        icon_row = QHBoxLayout()
        icon_row.addStretch(1)
        icon_row.addWidget(self._icon_label)
        icon_row.addStretch(1)
        outer.addLayout(icon_row)

        # --- title ----------------------------------------------------------
        self._title_label = QLabel(title, self)
        self._title_label.setProperty("textSize", "h4")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._title_label)

        # --- body -----------------------------------------------------------
        self._body_label = QLabel(body, self)
        self._body_label.setProperty("textSize", "body")
        self._body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._body_label.setWordWrap(True)
        self._body_label.setMaximumWidth(420)  # ~60ch at body size
        self._body_label.setStyleSheet(
            f"color: {tokens['color']['text']['secondary']};"
        )
        body_row = QHBoxLayout()
        body_row.addStretch(1)
        body_row.addWidget(self._body_label)
        body_row.addStretch(1)
        outer.addLayout(body_row)

        # --- action button --------------------------------------------------
        if action_button is not None:
            action_row = QHBoxLayout()
            action_row.addStretch(1)
            action_row.addWidget(action_button)
            action_row.addStretch(1)
            outer.addLayout(action_row)

        outer.addStretch(2)

    def actionButton(self) -> Button | None:
        return self._action_button


__all__ = ["EmptyState"]
