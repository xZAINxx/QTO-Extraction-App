"""CockpitWorkspace — bid-day cockpit view.

Wave 5 commit 9 of the dapper-pebble plan (section "5. Phase 1 features"
item #8). The cockpit is the focused interface for the final
pre-submission stretch: a giant total at the top with a deadline
countdown, a division-cost breakdown bar chart, an exclusions list,
three markup sliders (overhead / profit / contingency), a sub-bid
table, and the "Regenerate Proposal" button that wires up in Phase 3.

Persistence mirrors :mod:`ui.panels._scope_store` — a JSON file keyed by
PDF fingerprint so per-project state round-trips between sessions. The
:class:`_CockpitStore` helper stays module-internal because the schema
is small.

Markup math is **additive** (documented on :py:meth:`calculate_total`):
``total = base * (1 + (oh + profit + contingency) / 100)``. The compound
form would have made the rounded $12,500 acceptance test ambiguous and
estimators verbally describe the markup that way ("25 points total").
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPaintEvent, QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QSlider, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.qto_row import QTORow
from ui.components import Button, Card, Toaster
from ui.theme import tokens

_DEFAULT_OVERHEAD, _DEFAULT_PROFIT, _DEFAULT_CONTINGENCY = 8, 10, 5
_OVERHEAD_MAX, _PROFIT_MAX, _CONTINGENCY_MAX = 30, 25, 20
_TOTAL_AREA_HEIGHT = 120
_BAR_HEIGHT = 12
_COUNTDOWN_TICK_MS = 1000


@dataclass
class _CockpitStore:
    """JSON-backed per-project store, mirrors :class:`ScopeStore`.

    Layout: ``{"<fp>": {"exclusions", "sub_bids", "overhead_pct",
    "profit_pct", "contingency_pct"}}``.
    """

    cache_dir: Path
    fingerprint: str = ""
    data: dict[str, object] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.cache_dir / "cockpit.json"

    def load(self, pdf_fingerprint: str) -> dict[str, object]:
        self.fingerprint = pdf_fingerprint
        if not self.path.exists():
            self.data = {}
            return self.data
        try:
            blob = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            blob = {}
        self.data = dict(blob.get(pdf_fingerprint, {}))
        return self.data

    def save(self, payload: dict[str, object]) -> None:
        self.data = dict(payload)
        if not self.fingerprint:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            blob = json.loads(self.path.read_text()) if self.path.exists() else {}
        except (OSError, json.JSONDecodeError):
            blob = {}
        blob[self.fingerprint] = dict(self.data)
        self.path.write_text(json.dumps(blob, indent=2))


class _DivisionBar(QWidget):
    """Horizontal accent-colored fill bar — single paintEvent, no animation."""

    def __init__(self, ratio: float = 0.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ratio = max(0.0, min(1.0, float(ratio)))
        self.setFixedHeight(_BAR_HEIGHT)
        self.setMinimumWidth(60)

    def setRatio(self, ratio: float) -> None:
        self._ratio = max(0.0, min(1.0, float(ratio)))
        self.update()

    def ratio(self) -> float:
        return self._ratio

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect()
            painter.fillRect(rect, QColor(tokens["color"]["bg"]["surface"]["3"]))
            painter.fillRect(
                rect.x(), rect.y(), int(rect.width() * self._ratio), rect.height(),
                QColor(tokens["color"]["accent"]["default"]),
            )
        finally:
            painter.end()


class CockpitWorkspace(QWidget):
    """Bid-day cockpit workspace.

    Public API: :py:meth:`set_rows`, :py:meth:`set_project_name`,
    :py:meth:`set_deadline`, :py:meth:`set_pdf_fingerprint`,
    :py:meth:`calculate_total`. Signal:
    :py:attr:`proposal_export_requested` fires on the regenerate-button
    click; Phase-3 wiring listens to this.
    """

    proposal_export_requested = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        cache_dir: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache_dir = Path(cache_dir) if cache_dir else Path("./cache")
        self._store = _CockpitStore(cache_dir=self._cache_dir)
        self._rows: list[QTORow] = []
        self._by_division: dict[str, float] = {}
        self._project_name: str = ""
        self._deadline: Optional[datetime] = None
        # ``_loading`` guards against textChanged/valueChanged/itemChanged
        # writing back to disk during programmatic state restoration.
        self._loading: bool = False

        self._build_ui()
        self._refresh_total()
        self._update_countdown()

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(_COUNTDOWN_TICK_MS)
        self._countdown_timer.timeout.connect(self._update_countdown)
        self._countdown_timer.start()

    # ---- Layout assembly ------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        sp = tokens["space"][4]
        outer.setContentsMargins(sp, sp, sp, sp)
        outer.setSpacing(sp)
        outer.addWidget(self._build_total_card())
        outer.addWidget(self._build_body_splitter(), 1)

    def _build_total_card(self) -> Card:
        card = Card(elevation=2, parent=self)
        card.setObjectName("cockpitTotalCard")
        card.setMinimumHeight(_TOTAL_AREA_HEIGHT)
        body = card.body().layout()
        if body is not None:
            body.setSpacing(tokens["space"][1])

        self._project_label = self._mk_label(
            card, "", "cockpitProjectLabel", "h4",
        )
        card.addToBody(self._project_label)

        self._total_label = self._mk_label(
            card, "$0.00", "cockpitTotalLabel", "h2",
            extra=(
                f"font-family: {tokens['font']['family']['mono']}; "
                f"color: {tokens['color']['text']['primary']};"
            ),
        )
        card.addToBody(self._total_label)

        self._breakdown_label = self._mk_label(
            card, "Base + Markup", "cockpitBreakdownLabel", "body",
            extra=f"color: {tokens['color']['text']['secondary']};",
        )
        card.addToBody(self._breakdown_label)

        self._countdown_label = self._mk_label(
            card, "", "cockpitCountdownLabel", "body",
        )
        card.addToBody(self._countdown_label)
        return card

    @staticmethod
    def _mk_label(
        parent: QWidget, text: str, name: str, size: str, *, extra: str = "",
    ) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName(name)
        label.setProperty("textSize", size)
        if extra:
            label.setStyleSheet(extra)
        return label

    def _build_body_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setObjectName("cockpitBodySplitter")
        splitter.setHandleWidth(2)
        splitter.addWidget(self._build_left_column())
        splitter.addWidget(self._build_right_column())
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([600, 400])
        return splitter

    def _build_left_column(self) -> QWidget:
        col = QWidget(self)
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens["space"][4])
        layout.addWidget(self._build_division_card(), 1)
        layout.addWidget(self._build_exclusions_card(), 1)
        return col

    def _build_division_card(self) -> Card:
        card = Card(elevation=1, header_text="Cost by Division", parent=self)
        card.setObjectName("cockpitDivisionCard")
        self._division_host = QWidget(card)
        self._division_host.setObjectName("cockpitDivisionHost")
        self._division_layout = QVBoxLayout(self._division_host)
        self._division_layout.setContentsMargins(0, 0, 0, 0)
        self._division_layout.setSpacing(tokens["space"][2])
        card.addToBody(self._division_host)
        self._division_layout.addStretch(1)
        return card

    def _build_exclusions_card(self) -> Card:
        card = Card(elevation=1, header_text="Exclusions", parent=self)
        card.setObjectName("cockpitExclusionsCard")
        self._exclusions_edit = QPlainTextEdit(card)
        self._exclusions_edit.setObjectName("cockpitExclusionsEdit")
        self._exclusions_edit.setPlaceholderText(
            "Items NOT in this bid (one per line)…"
        )
        self._exclusions_edit.textChanged.connect(self._on_exclusions_changed)
        card.addToBody(self._exclusions_edit)
        return card

    def _build_right_column(self) -> QWidget:
        col = QWidget(self)
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens["space"][4])
        layout.addWidget(self._build_markup_card())
        layout.addWidget(self._build_sub_bids_card(), 1)
        layout.addWidget(self._build_regenerate_button())
        return col

    def _build_markup_card(self) -> Card:
        card = Card(elevation=1, header_text="Markup", parent=self)
        card.setObjectName("cockpitMarkupCard")
        self._overhead_slider, self._overhead_spin = self._make_slider(
            card, "Overhead", _OVERHEAD_MAX, _DEFAULT_OVERHEAD, "overhead",
        )
        self._profit_slider, self._profit_spin = self._make_slider(
            card, "Profit", _PROFIT_MAX, _DEFAULT_PROFIT, "profit",
        )
        self._contingency_slider, self._contingency_spin = self._make_slider(
            card, "Contingency", _CONTINGENCY_MAX, _DEFAULT_CONTINGENCY,
            "contingency",
        )
        return card

    def _make_slider(
        self, card: Card, label: str, max_pct: int, default: int, key: str,
    ) -> tuple[QSlider, QSpinBox]:
        row = QWidget(card)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens["space"][3])

        text = QLabel(label, row)
        text.setProperty("textSize", "body")
        text.setMinimumWidth(96)
        layout.addWidget(text)

        slider = QSlider(Qt.Orientation.Horizontal, row)
        slider.setObjectName(f"cockpit{key.title()}Slider")
        slider.setRange(0, max_pct)
        slider.setValue(default)
        layout.addWidget(slider, 1)

        spin = QSpinBox(row)
        spin.setObjectName(f"cockpit{key.title()}Spin")
        spin.setRange(0, max_pct)
        spin.setValue(default)
        spin.setSuffix("%")
        spin.setFixedWidth(72)
        layout.addWidget(spin)

        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        slider.valueChanged.connect(self._on_markup_changed)
        card.addToBody(row)
        return slider, spin

    def _build_sub_bids_card(self) -> Card:
        card = Card(elevation=1, header_text="Sub Bids", parent=self)
        card.setObjectName("cockpitSubBidsCard")
        self._sub_bids_table = QTableWidget(0, 2, card)
        self._sub_bids_table.setObjectName("cockpitSubBidsTable")
        self._sub_bids_table.setHorizontalHeaderLabels(["Trade", "Amount"])
        header = self._sub_bids_table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._sub_bids_table.itemChanged.connect(self._on_sub_bids_changed)
        card.addToBody(self._sub_bids_table)

        actions = QWidget(card)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        self._add_sub_bid_btn = Button(
            text="Add Sub Bid", icon_name="upload",
            variant="secondary", size="sm", parent=actions,
        )
        self._add_sub_bid_btn.setObjectName("cockpitAddSubBidBtn")
        self._add_sub_bid_btn.clicked.connect(self._add_sub_bid_row)
        actions_layout.addStretch(1)
        actions_layout.addWidget(self._add_sub_bid_btn)
        card.addToBody(actions)
        return card

    def _build_regenerate_button(self) -> Button:
        btn = Button(
            text="Regenerate Proposal", variant="primary", size="lg",
            icon_name="floppy-disk", parent=self,
        )
        btn.setObjectName("cockpitRegenerateBtn")
        btn.clicked.connect(self._on_regenerate_clicked)
        self._regenerate_btn = btn
        return btn

    # ---- Public API -----------------------------------------------------

    def set_rows(self, rows: list[QTORow]) -> None:
        """Recalculate the division breakdown and total from ``rows``."""
        self._rows = list(rows or [])
        self._by_division = self._compute_by_division(self._rows)
        self._refresh_division_breakdown()
        self._refresh_total()

    def set_project_name(self, name: str) -> None:
        self._project_name = name or ""
        self._project_label.setText(self._project_name)

    def set_deadline(self, deadline_iso: str | None) -> None:
        """Set ISO-8601 deadline. ``None`` clears the countdown."""
        if not deadline_iso:
            self._deadline = None
        else:
            try:
                # ``fromisoformat`` accepts offsets and bare local times.
                # Fold ``Z`` first since older Python only learned that
                # in 3.11.
                self._deadline = datetime.fromisoformat(
                    deadline_iso.replace("Z", "+00:00"),
                )
            except (TypeError, ValueError):
                self._deadline = None
        # Refresh once synchronously so callers / tests see the label
        # immediately without waiting for the 1s timer tick.
        self._update_countdown()

    def set_pdf_fingerprint(self, fingerprint: str) -> None:
        """Bind to ``fingerprint`` and restore that project's saved state."""
        payload = self._store.load(fingerprint)
        self._loading = True
        try:
            self._exclusions_edit.setPlainText(str(payload.get("exclusions", "")))
            self._overhead_slider.setValue(
                int(payload.get("overhead_pct", _DEFAULT_OVERHEAD))
            )
            self._profit_slider.setValue(
                int(payload.get("profit_pct", _DEFAULT_PROFIT))
            )
            self._contingency_slider.setValue(
                int(payload.get("contingency_pct", _DEFAULT_CONTINGENCY))
            )
            self._sub_bids_table.setRowCount(0)
            for entry in payload.get("sub_bids", []) or []:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    continue
                self._append_sub_bid_row(str(entry[0]), str(entry[1]))
        finally:
            self._loading = False
        self._refresh_total()

    def calculate_total(self) -> float:
        """Return ``base * (1 + (oh + profit + contingency) / 100)``.

        Markup is **additive** — see module docstring for rationale.
        """
        base = self._base_total()
        markup_pct = (
            self._overhead_slider.value()
            + self._profit_slider.value()
            + self._contingency_slider.value()
        )
        return base * (1.0 + markup_pct / 100.0)

    # ---- Internals ------------------------------------------------------

    def _base_total(self) -> float:
        rows_total = sum(
            float(r.qty or 0.0) * float(r.unit_price or 0.0) for r in self._rows
        )
        return rows_total + self._sub_bids_total()

    @staticmethod
    def _compute_by_division(rows: list[QTORow]) -> dict[str, float]:
        out: dict[str, float] = {}
        for row in rows:
            qty = float(row.qty or 0.0)
            price = float(row.unit_price or 0.0)
            div = (row.trade_division or "Uncategorized").strip() or "Uncategorized"
            out[div] = out.get(div, 0.0) + qty * price
        return out

    def _refresh_division_breakdown(self) -> None:
        # Clear all existing widgets except the trailing stretch.
        while self._division_layout.count() > 0:
            item = self._division_layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.deleteLater()
        ordered = sorted(
            self._by_division.items(), key=lambda kv: kv[1], reverse=True,
        )
        grand_total = sum(self._by_division.values()) or 1.0
        for division, amount in ordered:
            self._division_layout.addWidget(
                self._make_division_row(division, amount, amount / grand_total),
            )
        self._division_layout.addStretch(1)

    def _make_division_row(
        self, division: str, amount: float, ratio: float,
    ) -> QWidget:
        row = QWidget(self._division_host)
        row.setObjectName("cockpitDivisionRow")
        row.setProperty("divisionName", division)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens["space"][3])

        name = QLabel(division, row)
        name.setProperty("textSize", "body")
        name.setMinimumWidth(140)
        layout.addWidget(name)

        bar = _DivisionBar(ratio=ratio, parent=row)
        bar.setObjectName("cockpitDivisionBar")
        layout.addWidget(bar, 1)

        amt = QLabel(f"${amount:,.2f}", row)
        amt.setProperty("textSize", "body")
        amt.setStyleSheet(f"font-family: {tokens['font']['family']['mono']};")
        amt.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        amt.setMinimumWidth(110)
        layout.addWidget(amt)

        pct = QLabel(f"{ratio * 100:5.1f}%", row)
        pct.setProperty("textSize", "body-sm")
        pct.setStyleSheet(f"color: {tokens['color']['text']['secondary']};")
        pct.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        pct.setMinimumWidth(56)
        layout.addWidget(pct)
        return row

    def _refresh_total(self) -> None:
        self._total_label.setText(f"${self.calculate_total():,.2f}")
        markup_pct = (
            self._overhead_slider.value()
            + self._profit_slider.value()
            + self._contingency_slider.value()
        )
        self._breakdown_label.setText(
            f"Base ${self._base_total():,.2f}  +  Markup {markup_pct}%"
        )

    def _update_countdown(self) -> None:
        if self._deadline is None:
            self._countdown_label.setText("")
            self._countdown_label.setStyleSheet(
                f"color: {tokens['color']['text']['secondary']};"
            )
            return
        now = datetime.now(self._deadline.tzinfo) if self._deadline.tzinfo \
            else datetime.now()
        total_seconds = (self._deadline - now).total_seconds()
        if total_seconds <= 0:
            self._countdown_label.setText("PAST DUE")
            self._countdown_label.setStyleSheet(
                f"color: {tokens['color']['danger']}; font-weight: 600;"
            )
            return
        hours = int(total_seconds // 3600)
        mins = int((total_seconds % 3600) // 60)
        self._countdown_label.setText(f"Bid due in {hours:02d}h {mins:02d}m")
        self._countdown_label.setStyleSheet(
            f"color: {tokens['color']['text']['secondary']};"
        )

    # ---- Persistence handlers -------------------------------------------

    def _on_exclusions_changed(self) -> None:
        if self._loading:
            return
        self._save_state()

    def _on_markup_changed(self, _value: int) -> None:
        self._refresh_total()
        if self._loading:
            return
        self._save_state()

    def _on_sub_bids_changed(self, _item: QTableWidgetItem) -> None:
        self._refresh_total()
        if self._loading:
            return
        self._save_state()

    def _add_sub_bid_row(self) -> None:
        self._append_sub_bid_row("", "")

    def _append_sub_bid_row(self, trade: str, amount: str) -> None:
        row = self._sub_bids_table.rowCount()
        self._sub_bids_table.insertRow(row)
        self._sub_bids_table.setItem(row, 0, QTableWidgetItem(trade))
        self._sub_bids_table.setItem(row, 1, QTableWidgetItem(amount))

    def _sub_bids_total(self) -> float:
        total = 0.0
        for row in range(self._sub_bids_table.rowCount()):
            item = self._sub_bids_table.item(row, 1)
            if item is None:
                continue
            try:
                total += float((item.text() or "").replace(",", "").strip() or 0)
            except ValueError:
                continue
        return total

    def _save_state(self) -> None:
        sub_bids: list[list[str]] = []
        for row in range(self._sub_bids_table.rowCount()):
            trade_item = self._sub_bids_table.item(row, 0)
            amount_item = self._sub_bids_table.item(row, 1)
            sub_bids.append([
                trade_item.text() if trade_item is not None else "",
                amount_item.text() if amount_item is not None else "",
            ])
        self._store.save({
            "exclusions": self._exclusions_edit.toPlainText(),
            "sub_bids": sub_bids,
            "overhead_pct": self._overhead_slider.value(),
            "profit_pct": self._profit_slider.value(),
            "contingency_pct": self._contingency_slider.value(),
        })

    def _on_regenerate_clicked(self) -> None:
        self.proposal_export_requested.emit()
        Toaster.show("Proposal export wires in Phase 3", variant="info")


__all__ = ["CockpitWorkspace"]
