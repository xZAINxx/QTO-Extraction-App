"""CommandPalette — frameless ⌘K modal with fuzzy-search across the workspace.

Wave 5 commit 8 of the dapper-pebble plan ("Command palette ⌘K"). Inspired
by Linear / Raycast: a frameless dialog that overlays the parent window,
hosts a single large search input, and surfaces a unified ranked list of
rows, sheets, divisions, and registered commands.

Public surface:
    * :class:`CommandPalette` — the dialog widget. Open via ``palette.open()``.
    * :func:`build_palette_index` — combines rows + sheets + divisions +
      commands into the dict-list the palette consumes.

Design notes
============

* Fuzzy match runs through ``rapidfuzz.process.extract`` with the WRatio
  scorer over a flat ``"label subtitle"`` haystack. Mapping the result back
  to the original item dict happens via the index, so the original payload
  (row index, page number, callable, etc.) survives every score pass.
* Keyboard model: search input owns text, the **dialog** owns navigation
  keys. Arrow keys are intercepted at the dialog level so QLineEdit's
  default Up/Down handling doesn't swallow them. Enter and Escape are
  routed the same way.
* The palette never invokes a command's payload itself — it emits
  ``item_chosen`` with the chosen dict. The owning window decides what to
  do (call the callable, jump to a row, focus a sheet, etc.).
* Empty-query rule: show up to 8 commands, then up to 5 most recent rows,
  but if the index is short enough to fit, just show everything in
  insertion order. Tests rely on the "show everything when small" branch.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.theme import tokens

try:
    from rapidfuzz import fuzz, process  # type: ignore[import-untyped]
    _RAPIDFUZZ_AVAILABLE = True
except Exception:  # pragma: no cover — exercised when rapidfuzz is missing
    fuzz = None  # type: ignore[assignment]
    process = None  # type: ignore[assignment]
    _RAPIDFUZZ_AVAILABLE = False


# Layout constants — pinned so tests / siblings can read predictable sizes.
PALETTE_WIDTH = 600
PALETTE_HEIGHT = 420
MAX_VISIBLE_RESULTS = 8
EMPTY_QUERY_COMMANDS = 8
EMPTY_QUERY_ROWS = 5

# Map item type → (icon name, type pill label).
_TYPE_META: dict[str, tuple[str, str]] = {
    "row":      ("tag",              "ROW"),
    "sheet":    ("magnifying-glass", "SHEET"),
    "division": ("funnel",           "DIV"),
    "command":  ("command",          "CMD"),
}


# ---------------------------------------------------------------------------
# Index builder — pure function, callable from main_window without touching Qt.
# ---------------------------------------------------------------------------


def build_palette_index(
    *,
    rows: Optional[Iterable[Any]] = None,
    sheet_count: int = 0,
    sheet_titles: Optional[dict[int, str]] = None,
    divisions: Optional[Iterable[str]] = None,
    commands: Optional[Iterable[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Combine workspace state into the unified dict-list the palette renders.

    Parameters
    ----------
    rows
        Iterable of ``QTORow`` instances (anything with ``description``,
        ``source_sheet``, ``trade_division``, ``qty``, ``units``,
        ``source_page``). Header rows are skipped — they're section
        dividers, not searchable line items.
    sheet_count
        Total number of sheets in the loaded PDF. Drives sheet entries
        when ``sheet_titles`` doesn't list them all explicitly.
    sheet_titles
        Optional ``{page_num: title}`` mapping. Falls back to
        ``"Sheet {n}"`` when a page has no explicit title.
    divisions
        Iterable of unique trade-division strings (e.g. ``"DIV 09"``).
    commands
        Iterable of dicts with at least ``label`` (str), ``subtitle`` (str)
        and ``payload`` (any — usually a zero-arg callable).
    """
    out: list[dict[str, Any]] = []

    # Commands first — they're frequently chosen, and surfacing them at the
    # top of an empty-query result keeps the "Linear muscle memory" intact.
    for cmd in (commands or []):
        if not isinstance(cmd, dict):
            continue
        out.append(
            {
                "type":     "command",
                "label":    str(cmd.get("label", "")),
                "subtitle": str(cmd.get("subtitle", "")),
                "payload":  cmd.get("payload"),
            }
        )

    # Rows — derive a SaaS-style label/subtitle from the QTORow fields.
    for row in (rows or []):
        if getattr(row, "is_header_row", False):
            continue
        description = (getattr(row, "description", "") or "").strip()
        if not description:
            continue
        sheet = (getattr(row, "source_sheet", "") or "").strip()
        division = (getattr(row, "trade_division", "") or "").strip()
        qty = getattr(row, "qty", 0) or 0
        units = (getattr(row, "units", "") or "").strip()
        page = int(getattr(row, "source_page", 0) or 0)

        subtitle_parts = [p for p in (sheet, division, _qty_label(qty, units)) if p]
        out.append(
            {
                "type":     "row",
                "label":    description.upper(),
                "subtitle": " · ".join(subtitle_parts),
                "payload":  {"page": page, "sheet": sheet, "row": row},
            }
        )

    # Sheets — prefer the explicit title map, otherwise generate "Sheet N".
    titles = dict(sheet_titles or {})
    pages = sorted(set(titles.keys()) | set(range(1, sheet_count + 1)))
    for page in pages:
        title = (titles.get(page) or f"Sheet {page}").strip()
        out.append(
            {
                "type":     "sheet",
                "label":    title.upper(),
                "subtitle": f"Page {page}",
                "payload":  page,
            }
        )

    for division in (divisions or []):
        name = str(division or "").strip()
        if not name:
            continue
        out.append(
            {
                "type":     "division",
                "label":    name.upper(),
                "subtitle": "Filter takeoff by division",
                "payload":  name,
            }
        )

    return out


def _qty_label(qty: float, units: str) -> str:
    if not qty:
        return ""
    units = units or ""
    # Render integers without decimals; floats with two places.
    if abs(qty - round(qty)) < 1e-9:
        return f"{int(round(qty))} {units}".strip()
    return f"{qty:.2f} {units}".strip()


# ---------------------------------------------------------------------------
# Palette dialog
# ---------------------------------------------------------------------------


class CommandPalette(QDialog):
    """Frameless modal command palette with fuzzy search.

    Lifecycle:
        1. ``set_index(items)`` is called once per open with the current
           workspace state.
        2. ``open()`` shows the palette centered on the parent window and
           focuses the search field.
        3. The user types — results refilter on each keystroke (no
           debounce; 60Hz on small indices is fine and ``rapidfuzz`` is
           microseconds-fast for sub-1k items).
        4. Enter or click → ``item_chosen`` is emitted with the dict, then
           the palette closes. The parent decides what to do with the item.
    """

    item_chosen = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        # Use the standard Dialog window flag so QDialog's modal/event-loop
        # machinery works in tests (a Popup-only flag dismisses on focus
        # loss, which the offscreen platform fires immediately).
        super().__init__(parent)
        self.setObjectName("commandPalette")
        self.setWindowTitle("Command Palette")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
        )
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.resize(PALETTE_WIDTH, PALETTE_HEIGHT)
        self.setMinimumSize(PALETTE_WIDTH, PALETTE_HEIGHT)

        self._items: list[dict[str, Any]] = []
        self._build_ui()
        self._apply_palette_qss()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Search field — large, borderless, full-width.
        self._search = QLineEdit(self)
        self._search.setObjectName("paletteSearch")
        self._search.setPlaceholderText(
            "Type a command, row, sheet, or division…"
        )
        self._search.setClearButtonEnabled(False)
        self._search.textChanged.connect(self._on_text_changed)
        outer.addWidget(self._search)

        # Divider beneath the search input.
        divider = QFrame(self)
        divider.setObjectName("paletteDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        outer.addWidget(divider)

        # Results list.
        self._results = QListWidget(self)
        self._results.setObjectName("paletteResults")
        self._results.setUniformItemSizes(True)
        self._results.itemActivated.connect(self._on_item_activated)
        self._results.itemClicked.connect(self._on_item_activated)
        outer.addWidget(self._results, 1)

        # Footer with key hints.
        self._footer = QLabel(
            "↑↓ Navigate · ↵ Select · Esc Cancel",
            self,
        )
        self._footer.setObjectName("paletteFooter")
        self._footer.setProperty("textSize", "caption")
        self._footer.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        outer.addWidget(self._footer)

    def _apply_palette_qss(self) -> None:
        # Inline rules for the chrome that the global stylesheet doesn't
        # cover (frameless dialogs sit outside the normal QDialog rules).
        bg = tokens["color"]["bg"]["surface"]["raised"]
        border = tokens["color"]["border"]["default"]
        text_secondary = tokens["color"]["text"]["secondary"]
        text_primary = tokens["color"]["text"]["primary"]
        subtle = tokens["color"]["bg"]["surface"]["2"]
        self.setStyleSheet(
            f"""
            QDialog#commandPalette {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QLineEdit#paletteSearch {{
                background-color: transparent;
                border: none;
                color: {text_primary};
                padding: 18px 20px;
                font-size: 18px;
            }}
            QFrame#paletteDivider {{
                background-color: {border};
                border: none;
            }}
            QListWidget#paletteResults {{
                background-color: transparent;
                border: none;
                padding: 6px 8px;
                outline: none;
            }}
            QListWidget#paletteResults::item {{
                padding: 8px 10px;
                border-radius: 6px;
                color: {text_primary};
            }}
            QListWidget#paletteResults::item:selected {{
                background-color: {subtle};
                color: {text_primary};
            }}
            QLabel#paletteFooter {{
                color: {text_secondary};
                padding: 8px 14px;
                border-top: 1px solid {border};
            }}
            """
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_index(self, items: list[dict[str, Any]]) -> None:
        """Replace the searchable index. Triggers an immediate refilter."""
        self._items = list(items or [])
        # Reset query so a fresh open doesn't carry stale text.
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._apply_filter()

    def open(self) -> None:  # type: ignore[override]
        """Show the palette centered over the parent window and focus search."""
        self._center_over_parent()
        self.show()
        self.raise_()
        self.activateWindow()
        self._search.setFocus(Qt.FocusReason.PopupFocusReason)
        # Auto-select first row so Enter "just works" with no extra clicks.
        if self._results.count() > 0:
            self._results.setCurrentRow(0)

    def search_input(self) -> QLineEdit:
        return self._search

    def results_widget(self) -> QListWidget:
        return self._results

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _on_text_changed(self, _text: str) -> None:
        # Trivial debounce: collapse storms of textChanged into one tick.
        QTimer.singleShot(0, self._apply_filter)

    def _apply_filter(self) -> None:
        query = self._search.text().strip()
        ranked = self._rank_items(query)
        self._render_results(ranked)

    def _rank_items(self, query: str) -> list[dict[str, Any]]:
        if not self._items:
            return []
        if not query:
            # Empty-query: prefer commands first, then rows, but never starve
            # a small index — show everything if it fits inside the cap.
            if len(self._items) <= MAX_VISIBLE_RESULTS:
                return list(self._items)
            commands = [it for it in self._items if it.get("type") == "command"]
            rows = [it for it in self._items if it.get("type") == "row"]
            others = [
                it for it in self._items
                if it.get("type") not in ("command", "row")
            ]
            picked: list[dict[str, Any]] = []
            picked.extend(commands[:EMPTY_QUERY_COMMANDS])
            picked.extend(rows[:EMPTY_QUERY_ROWS])
            picked.extend(others[: max(0, MAX_VISIBLE_RESULTS - len(picked))])
            return picked

        if not _RAPIDFUZZ_AVAILABLE or process is None:
            # Defensive fallback — substring match only. Tests always have
            # rapidfuzz available, so this branch isn't covered.
            lower = query.lower()
            return [
                it for it in self._items
                if lower in (it.get("label") or "").lower()
                or lower in (it.get("subtitle") or "").lower()
            ][:MAX_VISIBLE_RESULTS * 2]

        # Lowercase both query and haystack — WRatio is case-sensitive and
        # workspace labels are conventionally uppercase ("GYPSUM BOARD"),
        # so without normalising the user's lowercase typing scores to zero.
        normalized_query = query.lower()
        haystack = [
            f"{it.get('label', '')} {it.get('subtitle', '')}".lower()
            for it in self._items
        ]
        matches = process.extract(
            normalized_query, haystack,
            scorer=fuzz.WRatio,
            limit=20,
        )
        # rapidfuzz returns (matched_string, score, original_index).
        # Noise floor 60 — below that WRatio is essentially "two chars happened
        # to coincide" rather than a meaningful prefix or substring hit.
        ranked: list[dict[str, Any]] = []
        for _string, score, original_idx in matches:
            if score < 60:
                continue
            ranked.append(self._items[original_idx])
        return ranked

    def _render_results(self, ranked: list[dict[str, Any]]) -> None:
        self._results.clear()
        # Cap the visible count but the proxy matches were already capped at 20.
        for item_dict in ranked[: MAX_VISIBLE_RESULTS * 2]:
            label = item_dict.get("label", "")
            subtitle = item_dict.get("subtitle", "")
            type_key = item_dict.get("type", "")
            _icon_name, type_label = _TYPE_META.get(type_key, ("info", "?"))
            display = label
            if subtitle:
                display = f"{label}    {subtitle}    [{type_label}]"
            list_item = QListWidgetItem(display, self._results)
            list_item.setData(Qt.ItemDataRole.UserRole, item_dict)
        if self._results.count() > 0:
            self._results.setCurrentRow(0)

    # ------------------------------------------------------------------
    # Keyboard handling — own the dialog-level keys before children.
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: D401
        key = event.key()
        if key == Qt.Key.Key_Escape:
            event.accept()
            self.close()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._activate_current()
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            self._move_selection(+1)
            event.accept()
            return
        if key == Qt.Key.Key_Up:
            self._move_selection(-1)
            event.accept()
            return
        super().keyPressEvent(event)

    def _move_selection(self, delta: int) -> None:
        count = self._results.count()
        if count == 0:
            return
        current = self._results.currentRow()
        if current < 0:
            current = 0
        new = max(0, min(count - 1, current + delta))
        self._results.setCurrentRow(new)

    def _activate_current(self) -> None:
        current = self._results.currentItem()
        if current is None:
            return
        self._on_item_activated(current)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        chosen = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(chosen, dict):
            return
        # Emit BEFORE closing — handlers like "Open PDF…" pop a QFileDialog
        # and the palette stack should already be unwinding when that
        # dialog appears.
        self.item_chosen.emit(chosen)
        self.close()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _center_over_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        parent_rect = parent.frameGeometry()
        self_rect = self.frameGeometry()
        center = parent_rect.center()
        self_rect.moveCenter(center)
        # Keep the palette anchored a touch above center for that Linear feel.
        top_left = self_rect.topLeft()
        top_left.setY(max(parent_rect.top() + 80, top_left.y() - 40))
        self.move(top_left)


__all__ = ["CommandPalette", "build_palette_index"]
