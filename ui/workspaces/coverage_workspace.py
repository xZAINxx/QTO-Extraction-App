"""CoverageWorkspace — anti-miss insurance for takeoff completeness.

Wave 6 commit 11 of the dapper-pebble plan (section "6. Phase 2 features"
item #10). The estimator's deepest fear is a forgotten division or a
plan sheet that silently never produced a single line item. This
workspace surfaces both: a CSI division breakdown that flags the empty
divisions FIRST (most urgent), and a sheet roster that calls out the
plan/schedule sheets that should have produced rows but didn't.

The CSI division list is hardcoded here to mirror the canonical
``csi_keywords`` block in ``config.yaml``. Drift is intentional only when
``config.yaml`` changes — keep the two in sync as a deliberate gesture.

No persistence: coverage is purely derived state from ``QTORow`` and
sheet-classification dicts. Re-render on every ``set_rows`` /
``set_sheets`` / ``refresh`` call.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPaintEvent, QPainter
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from core.qto_row import QTORow
from ui.components import Button, Card, Pill
from ui.theme import icon as theme_icon, tokens


# Mirrors ``config.yaml::csi_keywords`` — kept hardcoded so coverage can
# render before any config has been loaded. The plan calls these out as
# the "16 divisions" baseline; reconciliation with config is intentional.
_CSI_DIVISIONS: tuple[str, ...] = (
    "DIVISION 02", "DIVISION 03", "DIVISION 04", "DIVISION 05",
    "DIVISION 06", "DIVISION 07", "DIVISION 08", "DIVISION 09",
    "DIVISION 21", "DIVISION 22", "DIVISION 23", "DIVISION 26",
    "DIVISION 27", "DIVISION 28", "DIVISION 31", "DIVISION 32",
)

# Sheet page-types that are EXPECTED to produce takeoff rows. A sheet
# classified as one of these but with zero extracted rows is suspicious.
_PRODUCTIVE_PAGE_TYPES: frozenset[str] = frozenset({
    "PLAN_DEMO", "PLAN_CONSTRUCTION", "SCHEDULE",
})

_BAR_HEIGHT = 8
_DIVISION_NAME_WIDTH = 140
_COUNT_COL_WIDTH = 56
_SUBTOTAL_COL_WIDTH = 110


class _MiniBar(QWidget):
    """Horizontal accent fill bar — mirrors ``CockpitWorkspace._DivisionBar``."""

    def __init__(self, ratio: float = 0.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ratio = max(0.0, min(1.0, float(ratio)))
        self.setFixedHeight(_BAR_HEIGHT)
        self.setMinimumWidth(48)

    def setRatio(self, ratio: float) -> None:
        self._ratio = max(0.0, min(1.0, float(ratio)))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect()
            painter.fillRect(rect, QColor(tokens["color"]["bg"]["surface"]["3"]))
            painter.fillRect(
                rect.x(), rect.y(),
                int(rect.width() * self._ratio), rect.height(),
                QColor(tokens["color"]["accent"]["default"]),
            )
        finally:
            painter.end()


class CoverageWorkspace(QWidget):
    """Coverage / "holes" report.

    Public API: :py:meth:`set_rows`, :py:meth:`set_sheets`,
    :py:meth:`set_project_name`, :py:meth:`refresh`. Signal:
    :py:attr:`refresh_requested` fires on the topbar refresh button.
    """

    refresh_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: list[QTORow] = []
        self._sheets: dict[int, dict] = {}
        self._project_name: str = ""
        self._by_division: dict[str, tuple[int, float]] = {}
        self._by_sheet: dict[str, int] = {}

        self._build_ui()
        self.refresh()

    # ---- Layout ---------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        sp = tokens["space"][4]
        outer.setContentsMargins(sp, sp, sp, sp)
        outer.setSpacing(sp)
        outer.addWidget(self._build_topbar())
        outer.addWidget(self._build_body_splitter(), 1)
        outer.addWidget(self._build_summary_card())

    def _build_topbar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("coverageTopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens["space"][3])

        title = QLabel("Coverage Report", bar)
        title.setObjectName("coverageTitle")
        title.setProperty("textSize", "h4")
        layout.addWidget(title)

        self._refresh_btn = Button(
            text="Refresh", icon_name="arrows-clockwise",
            variant="ghost", size="sm", parent=bar,
        )
        self._refresh_btn.setObjectName("coverageRefreshBtn")
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        layout.addWidget(self._refresh_btn)

        layout.addStretch(1)

        self._project_label = QLabel("", bar)
        self._project_label.setObjectName("coverageProjectLabel")
        self._project_label.setProperty("textSize", "body")
        self._project_label.setStyleSheet(
            f"color: {tokens['color']['text']['secondary']};"
        )
        self._project_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self._project_label)
        return bar

    def _build_body_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setObjectName("coverageBodySplitter")
        splitter.setHandleWidth(2)
        splitter.addWidget(self._build_division_card())
        splitter.addWidget(self._build_sheet_card())
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([600, 400])
        return splitter

    def _build_division_card(self) -> Card:
        card = Card(elevation=1, header_text="Division Coverage", parent=self)
        card.setObjectName("coverageDivisionCard")
        scroll = QScrollArea(card)
        scroll.setObjectName("coverageDivisionScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        host = QWidget(scroll)
        host.setObjectName("coverageDivisionHost")
        self._division_layout = QVBoxLayout(host)
        self._division_layout.setContentsMargins(0, 0, 0, 0)
        self._division_layout.setSpacing(tokens["space"][2])
        self._division_layout.addStretch(1)
        scroll.setWidget(host)
        card.addToBody(scroll)
        return card

    def _build_sheet_card(self) -> Card:
        card = Card(elevation=1, header_text="Sheet Coverage", parent=self)
        card.setObjectName("coverageSheetCard")
        scroll = QScrollArea(card)
        scroll.setObjectName("coverageSheetScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        host = QWidget(scroll)
        host.setObjectName("coverageSheetHost")
        self._sheet_layout = QVBoxLayout(host)
        self._sheet_layout.setContentsMargins(0, 0, 0, 0)
        self._sheet_layout.setSpacing(tokens["space"][2])
        self._sheet_layout.addStretch(1)
        scroll.setWidget(host)
        card.addToBody(scroll)
        return card

    def _build_summary_card(self) -> Card:
        card = Card(elevation=1, parent=self)
        card.setObjectName("coverageSummaryCard")
        self._summary_label = QLabel("", card)
        self._summary_label.setObjectName("coverageSummaryLabel")
        self._summary_label.setProperty("textSize", "body")
        self._summary_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        card.addToBody(self._summary_label)
        return card

    # ---- Public API -----------------------------------------------------

    def set_rows(self, rows: list[QTORow]) -> None:
        """Replace the working row set and re-render coverage."""
        self._rows = list(rows or [])
        self.refresh()

    def set_sheets(self, page_classifications: dict[int, dict]) -> None:
        """Set per-page classifications.

        ``page_classifications`` maps ``page_num`` →
        ``{"page_type": str, "sheet_id": str, "skip": bool}``.
        """
        self._sheets = dict(page_classifications or {})
        self.refresh()

    def set_project_name(self, name: str) -> None:
        self._project_name = name or ""
        self._project_label.setText(self._project_name)

    def refresh(self) -> None:
        """Recompute every aggregate and re-render the panels."""
        self._by_division = self._compute_division_breakdown(self._rows)
        self._by_sheet = self._compute_sheet_row_counts(self._rows)
        self._render_divisions()
        self._render_sheets()
        self._render_summary()

    # ---- Internals — pure data ------------------------------------------

    @staticmethod
    def _compute_division_breakdown(
        rows: list[QTORow],
    ) -> dict[str, tuple[int, float]]:
        """Return ``{division: (row_count, dollar_subtotal)}``.

        Rows whose ``trade_division`` does not match a CSI bucket are
        bucketed under their raw division string so the user still sees
        the data, but only the canonical CSI divisions can be flagged
        as "empty" (the holes-report semantic).
        """
        out: dict[str, tuple[int, float]] = {}
        for row in rows:
            if row.is_header_row:
                continue
            div = (row.trade_division or "Uncategorized").strip() or "Uncategorized"
            count, subtotal = out.get(div, (0, 0.0))
            qty = float(row.qty or 0.0)
            price = float(row.unit_price or 0.0)
            out[div] = (count + 1, subtotal + qty * price)
        return out

    @staticmethod
    def _compute_sheet_row_counts(rows: list[QTORow]) -> dict[str, int]:
        """Return ``{sheet_number: row_count}`` keyed by ``source_sheet``."""
        out: dict[str, int] = {}
        for row in rows:
            if row.is_header_row:
                continue
            sheet = (row.source_sheet or "").strip()
            if not sheet:
                continue
            out[sheet] = out.get(sheet, 0) + 1
        return out

    # ---- Render — divisions --------------------------------------------

    def _render_divisions(self) -> None:
        self._clear_layout(self._division_layout)

        # Build the canonical-plus-extra union; canonical divisions come
        # first so empties stay surfaced even if the rows mention only
        # off-list custom divisions.
        canonical = list(_CSI_DIVISIONS)
        extras = sorted(
            d for d in self._by_division.keys() if d not in _CSI_DIVISIONS
        )
        all_divisions = canonical + extras

        # Compute the visible count maximum for bar scaling.
        max_count = max(
            (self._by_division.get(d, (0, 0.0))[0] for d in all_divisions),
            default=0,
        )
        scale_denom = max(max_count, 1)

        # Sort: empty canonical divisions first (most urgent), then by
        # ascending row count among the rest. Extras come last in their
        # own ascending order.
        def _sort_key(d: str) -> tuple[int, int, str]:
            count, _ = self._by_division.get(d, (0, 0.0))
            is_canonical = 0 if d in _CSI_DIVISIONS else 1
            empty_priority = 0 if (count == 0 and is_canonical == 0) else 1
            return (empty_priority, count, d)

        for division in sorted(all_divisions, key=_sort_key):
            count, subtotal = self._by_division.get(division, (0, 0.0))
            self._division_layout.insertWidget(
                self._division_layout.count() - 1,
                self._make_division_row(division, count, subtotal, count / scale_denom),
            )

    def _make_division_row(
        self, division: str, count: int, subtotal: float, ratio: float,
    ) -> QWidget:
        row = QWidget()
        row.setObjectName("coverageDivisionRow")
        row.setProperty("divisionName", division)
        row.setProperty("rowCount", count)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens["space"][3])

        name = QLabel(division, row)
        name.setProperty("textSize", "body")
        name.setMinimumWidth(_DIVISION_NAME_WIDTH)
        layout.addWidget(name)

        bar = _MiniBar(ratio=ratio, parent=row)
        bar.setObjectName("coverageDivisionBar")
        layout.addWidget(bar, 1)

        count_label = QLabel(f"{count}", row)
        count_label.setProperty("textSize", "body")
        count_label.setStyleSheet(
            f"font-family: {tokens['font']['family']['mono']};"
        )
        count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        count_label.setMinimumWidth(_COUNT_COL_WIDTH)
        layout.addWidget(count_label)

        subtotal_label = QLabel(f"${subtotal:,.0f}", row)
        subtotal_label.setProperty("textSize", "body")
        subtotal_label.setStyleSheet(
            f"font-family: {tokens['font']['family']['mono']};"
        )
        subtotal_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        subtotal_label.setMinimumWidth(_SUBTOTAL_COL_WIDTH)
        layout.addWidget(subtotal_label)

        if count == 0 and division in _CSI_DIVISIONS:
            pill = Pill(text="ZERO ROWS", variant="danger", parent=row)
            pill.setObjectName("coverageDivisionEmptyPill")
            layout.addWidget(pill)
        return row

    # ---- Render — sheets ------------------------------------------------

    def _render_sheets(self) -> None:
        self._clear_layout(self._sheet_layout)

        # Sort sheets by page number for determinism.
        for page_num in sorted(self._sheets.keys()):
            payload = self._sheets[page_num] or {}
            sheet_id = str(payload.get("sheet_id") or f"Page {page_num}")
            page_type = str(payload.get("page_type") or "").upper()
            skipped = bool(payload.get("skip", False))
            count = self._by_sheet.get(sheet_id, 0)
            self._sheet_layout.insertWidget(
                self._sheet_layout.count() - 1,
                self._make_sheet_row(sheet_id, count, page_type, skipped),
            )

    def _make_sheet_row(
        self, sheet_id: str, count: int, page_type: str, skipped: bool,
    ) -> QWidget:
        row = QWidget()
        row.setObjectName("coverageSheetRow")
        row.setProperty("sheetId", sheet_id)
        row.setProperty("pageType", page_type)
        row.setProperty("rowCount", count)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens["space"][3])

        sid = QLabel(sheet_id, row)
        sid.setProperty("textSize", "body")
        sid.setStyleSheet(f"font-family: {tokens['font']['family']['mono']};")
        sid.setMinimumWidth(96)
        layout.addWidget(sid)

        ptype = QLabel(page_type or "—", row)
        ptype.setProperty("textSize", "body-sm")
        ptype.setStyleSheet(
            f"color: {tokens['color']['text']['secondary']};"
        )
        layout.addWidget(ptype, 1)

        count_label = QLabel(f"{count}", row)
        count_label.setProperty("textSize", "body")
        count_label.setStyleSheet(
            f"font-family: {tokens['font']['family']['mono']};"
        )
        count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        count_label.setMinimumWidth(_COUNT_COL_WIDTH)
        layout.addWidget(count_label)

        # Tagging logic:
        #   * SKIPPED page (title page, non-productive)            → neutral "SKIPPED"
        #   * Productive page (PLAN_DEMO/CONSTRUCTION/SCHEDULE)
        #     with zero rows                                       → danger "MAYBE MISSED"
        #   * Other zero-row sheet                                 → neutral "SKIPPED"
        if count == 0:
            if skipped:
                pill = Pill(text="SKIPPED", variant="neutral", parent=row)
                pill.setObjectName("coverageSheetSkippedPill")
                layout.addWidget(pill)
            elif page_type in _PRODUCTIVE_PAGE_TYPES:
                pill = Pill(text="MAYBE MISSED", variant="danger", parent=row)
                pill.setObjectName("coverageSheetMaybeMissedPill")
                layout.addWidget(pill)
            else:
                pill = Pill(text="SKIPPED", variant="neutral", parent=row)
                pill.setObjectName("coverageSheetSkippedPill")
                layout.addWidget(pill)
        return row

    # ---- Render — summary -----------------------------------------------

    def _render_summary(self) -> None:
        total_rows = sum(c for c, _ in self._by_division.values())
        non_empty_divisions = sum(
            1 for d in _CSI_DIVISIONS if self._by_division.get(d, (0, 0.0))[0] > 0
        )
        sheet_count = len(self._sheets)
        # Coverage percentage: of canonical divisions, what fraction
        # have at least one row? Simple, useful, and avoids weighing
        # divisions by row count which is misleading at this stage.
        if _CSI_DIVISIONS:
            coverage_pct = (non_empty_divisions / len(_CSI_DIVISIONS)) * 100.0
        else:
            coverage_pct = 0.0
        self._summary_label.setText(
            f"{total_rows} rows across {non_empty_divisions} divisions and "
            f"{sheet_count} sheets · {coverage_pct:.0f}% coverage"
        )

    # ---- Helpers --------------------------------------------------------

    @staticmethod
    def _clear_layout(layout) -> None:
        """Remove every non-stretch child widget; keep the trailing stretch."""
        # Walk from the front so indices stay valid; stop one before the
        # final addStretch() entry.
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _on_refresh_clicked(self) -> None:
        self.refresh_requested.emit()
        self.refresh()


__all__ = ["CoverageWorkspace"]
