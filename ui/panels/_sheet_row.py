"""Per-sheet row widget for ``SheetRail``.

Pulled out of ``sheet_rail.py`` to keep that module under the 400-line
budget. ``_SheetRow`` is a composite of:

    * ``_ThumbHolder`` — fixed-size frame painting the thumbnail pixmap
      plus the scope-status dot in the bottom-right corner.
    * Discipline pill overlaid on the top-right of the thumbnail.
    * Sheet number + sheet title labels.
    * Optional revision pill if the row's metadata carries one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPaintEvent, QPixmap
from PyQt6.QtWidgets import QFrame, QLabel, QMenu, QVBoxLayout, QWidget

from ui.components import Pill, Skeleton
from ui.theme import tokens

ScopeStatus = Literal["in", "out", "deferred", "done"]
_SCOPE_VALUES: tuple[ScopeStatus, ...] = ("in", "out", "deferred", "done")
_DISCIPLINE_PLACEHOLDER = "?"

THUMB_EXPANDED = QSize(180, 140)
THUMB_COLLAPSED = QSize(48, 36)


@dataclass
class SheetMeta:
    page_num: int  # 1-based
    sheet_number: str = ""
    sheet_title: str = ""
    revision: str = ""
    discipline: str = ""

    def label(self) -> str:
        return self.sheet_number or f"Page {self.page_num}"


def scope_color(status: ScopeStatus) -> str:
    palette = tokens["color"]
    return {
        "in": palette["confirmed-yellow"],
        "out": palette["scope-out"],
        "deferred": palette["revision-pink"],
        "done": palette["approved-green"],
    }[status]


class _ThumbHolder(QFrame):
    """Fixed-size frame that paints the thumbnail + scope dot."""

    _DOT_DIAMETER = 10

    def __init__(self, size: QSize, scope: ScopeStatus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("thumbHolder")
        self.setFixedSize(size)
        self._pixmap: Optional[QPixmap] = None
        self._scope: ScopeStatus = scope
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._skeleton = Skeleton(shape="block", parent=self)
        layout.addWidget(self._skeleton)

    def set_size(self, size: QSize) -> None:
        self.setFixedSize(size)
        self._skeleton.setFixedSize(size)

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pixmap = pix
        self._skeleton.hide()
        self.update()

    def set_scope(self, scope: ScopeStatus) -> None:
        self._scope = scope
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._pixmap is not None and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(scope_color(self._scope)))
        d = self._DOT_DIAMETER
        painter.drawEllipse(self.width() - d - 4, self.height() - d - 4, d, d)
        painter.end()


class SheetRow(QFrame):
    """Single row in the rail — thumbnail + meta strip."""

    clicked = pyqtSignal(int)
    scope_changed = pyqtSignal(int, str)

    def __init__(self, meta: SheetMeta, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sheetRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("active", False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.meta = meta
        self.scope: ScopeStatus = "in"
        self._collapsed = False
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            tokens["space"][2], tokens["space"][2],
            tokens["space"][2], tokens["space"][2],
        )
        outer.setSpacing(tokens["space"][1])

        self._thumb_holder = _ThumbHolder(THUMB_EXPANDED, self.scope, self)
        self._discipline_pill = Pill(
            self.meta.discipline or _DISCIPLINE_PLACEHOLDER,
            variant="neutral",
            parent=self._thumb_holder,
        )
        self._discipline_pill.move(
            THUMB_EXPANDED.width() - 36, tokens["space"][1],
        )
        outer.addWidget(self._thumb_holder)

        self._number_label = QLabel(self.meta.label(), self)
        self._number_label.setStyleSheet(
            f"font-weight: 600; color: {tokens['color']['text']['primary']};"
        )
        outer.addWidget(self._number_label)

        if self.meta.sheet_title:
            self._title_label: Optional[QLabel] = QLabel(self.meta.sheet_title, self)
            self._title_label.setWordWrap(True)
            self._title_label.setMaximumHeight(36)
            self._title_label.setStyleSheet(
                f"color: {tokens['color']['text']['secondary']}; font-size: 12px;"
            )
            outer.addWidget(self._title_label)
        else:
            self._title_label = None

        if self.meta.revision:
            self._revision_pill: Optional[Pill] = Pill(
                f"Rev {self.meta.revision}", variant="warning", parent=self,
            )
            outer.addWidget(self._revision_pill)
        else:
            self._revision_pill = None

    # --- Public API ----------------------------------------------------------

    def set_thumbnail(self, pix: QPixmap) -> None:
        self._thumb_holder.set_pixmap(pix)

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        size = THUMB_COLLAPSED if collapsed else THUMB_EXPANDED
        self._thumb_holder.set_size(size)
        for w in (self._number_label, self._title_label, self._revision_pill):
            if w is not None:
                w.setVisible(not collapsed)
        self._discipline_pill.setVisible(not collapsed)
        self.setToolTip(self.meta.label() if collapsed else "")

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_scope(self, status: ScopeStatus, *, emit: bool = True) -> None:
        if status not in _SCOPE_VALUES:
            raise ValueError(f"unknown scope status: {status!r}")
        self.scope = status
        self._thumb_holder.set_scope(status)
        if emit:
            self.scope_changed.emit(self.meta.page_num, status)

    def matches(self, query: str, discipline: Optional[str]) -> bool:
        if discipline is not None and discipline != self.meta.discipline:
            return False
        if not query:
            return True
        haystack = f"{self.meta.sheet_number} {self.meta.sheet_title}".lower()
        return query.lower() in haystack

    # --- Events --------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.meta.page_num)
        super().mousePressEvent(event)

    def _on_context_menu(self, pos) -> None:  # type: ignore[no-untyped-def]
        menu = QMenu(self)
        labels: dict[ScopeStatus, str] = {
            "in": "In Scope",
            "out": "Out of Scope",
            "deferred": "Deferred",
            "done": "Done",
        }
        for status, label in labels.items():
            action = menu.addAction(label)
            action.setData(status)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is not None:
            self.set_scope(chosen.data())


__all__ = [
    "ScopeStatus",
    "SheetMeta",
    "SheetRow",
    "THUMB_COLLAPSED",
    "THUMB_EXPANDED",
    "scope_color",
]
