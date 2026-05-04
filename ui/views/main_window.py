"""MainWindow — the modern shell. Topbar + nav rail + workspace + dock strip.

Wave 3 of the dapper-pebble plan (section "4. Layout architecture"). This
commit lands the layout shell only; Inspector drill-down, Diff / Cockpit /
Coverage workspaces, the command palette, and full Run-Extraction wiring
arrive in later commits. ``ExtractionWorker`` is already importable from
``ui.controllers.extraction_worker`` so future wiring is a delta, not a
refactor.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.components import Button, EmptyState, Pill, Toaster
from ui.components.command_palette import CommandPalette, build_palette_index
from ui.controllers.trace_link import TraceLink
from ui.panels.calibration_dialog import CalibrationDialog
from ui.panels.sheet_rail import SheetRail
from ui.theme import apply_theme, icon as theme_icon, tokens
from ui.workspaces.cockpit_workspace import CockpitWorkspace
from ui.workspaces.coverage_workspace import CoverageWorkspace
from ui.workspaces.diff_workspace import DiffWorkspace
from ui.workspaces.takeoff_workspace import TakeoffWorkspace


# Layout constants. Tests pin these without grepping the layout code.
TOPBAR_HEIGHT = 56
NAV_RAIL_WIDTH = 56
DOCK_STRIP_HEIGHT = 44
INSPECTOR_WIDTH = 320


def _surface_border_qss(
    object_name: str, surface_key: str, side: str,
) -> str:
    """Build an inline QSS rule that paints a surface bg + one hairline border.

    Used by the topbar / nav-rail / dock-strip / inspector frames so each
    builder stays small. ``side`` is ``"bottom"``, ``"top"``, ``"left"`` or
    ``"right"`` — the side of the frame that gets the divider line.
    """
    surface_color = tokens["color"]["bg"]["surface"][surface_key]
    border_color = tokens["color"]["border"]["subtle"]
    return (
        f"#{object_name} {{ "
        f"background-color: {surface_color}; "
        f"border-{side}: 1px solid {border_color}; "
        f"}}"
    )


class MainWindow(QMainWindow):
    """Shell-only MainWindow for the new ``ui_v2`` flag."""

    def __init__(
        self,
        config: dict,
        app_dir: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config or {}
        self._app_dir = app_dir or str(Path(__file__).resolve().parents[2])
        self._pdf_path: Optional[str] = None
        self._theme_mode: str = "dark"
        # TraceLink and per-page zone cache — populated lazily on first use.
        self._trace_link: Optional[TraceLink] = None
        self._zone_cache: dict[int, object] = {}
        # Command palette — constructed lazily so its style cost is only paid
        # once the user opens it. ``_open_command_palette`` builds it on demand.
        self._command_palette: Optional[CommandPalette] = None
        self._palette_shortcut: Optional[QShortcut] = None

        self.setWindowTitle("Zeconic QTO")
        self.resize(1440, 900)
        self.setMinimumSize(1024, 680)

        # Apply theme. ``apply_theme`` is idempotent and tolerant of
        # multiple QApplication instances during testing.
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode=self._theme_mode)

        self._build_ui()
        self._build_menus()
        self._wire_signals()

    # ------------------------------------------------------------------
    # Layout assembly
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._topbar = self._build_topbar()
        root.addWidget(self._topbar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._nav_rail = self._build_nav_rail()
        body.addWidget(self._nav_rail)

        self._sheet_rail = self._build_sheet_rail()
        body.addWidget(self._sheet_rail)

        self._workspace_host = self._build_workspace_host()
        body.addWidget(self._workspace_host, 1)

        self._inspector = self._build_inspector_placeholder()
        body.addWidget(self._inspector)

        root.addLayout(body, 1)

        self._dock_strip = self._build_dock_strip()
        root.addWidget(self._dock_strip)

        self.setCentralWidget(central)

    # --- Topbar -------------------------------------------------------

    def _build_topbar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("topbar")
        bar.setProperty("surface", "1")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(TOPBAR_HEIGHT)
        bar.setStyleSheet(_surface_border_qss("topbar", "1", "bottom"))

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(
            tokens["space"][4], tokens["space"][2],
            tokens["space"][4], tokens["space"][2],
        )
        layout.setSpacing(tokens["space"][3])

        logo = QLabel("Zeconic QTO", bar)
        logo.setProperty("textSize", "h5")
        layout.addWidget(logo)

        self._project_btn = Button(
            text="Untitled Project ▾", variant="ghost", size="sm", parent=bar,
        )
        layout.addWidget(self._project_btn)

        mode_text = str(self._config.get("extraction_mode", "hybrid")).upper()
        self._mode_badge = Pill(text=mode_text, variant="info", parent=bar)
        self._mode_badge.setObjectName("modeBadge")
        layout.addWidget(self._mode_badge)

        layout.addStretch(1)

        self._cmd_palette_btn = Button(
            text="⌘K Search",
            icon_name="command",
            variant="ghost",
            size="sm",
            parent=bar,
        )
        self._cmd_palette_btn.setObjectName("cmdPaletteBtn")
        self._cmd_palette_btn.clicked.connect(self._open_command_palette)
        layout.addWidget(self._cmd_palette_btn)

        self._compare_btn = Button(
            icon_name="git-diff",
            variant="ghost",
            size="md",
            parent=bar,
        )
        self._compare_btn.setObjectName("compareBtn")
        self._compare_btn.setToolTip("Compare with another PDF set")
        self._compare_btn.clicked.connect(self._on_compare_with)
        layout.addWidget(self._compare_btn)

        self._zone_overlay_btn = Button(
            icon_name="frame-corners",
            variant="ghost",
            size="md",
            parent=bar,
        )
        self._zone_overlay_btn.setObjectName("zoneOverlayBtn")
        self._zone_overlay_btn.setToolTip("Toggle zone overlay")
        self._zone_overlay_btn.setCheckable(True)
        self._zone_overlay_btn.clicked.connect(self._on_toggle_zone_overlay)
        layout.addWidget(self._zone_overlay_btn)

        self._calibrate_btn = Button(
            icon_name="compass-tool",
            variant="ghost",
            size="md",
            parent=bar,
        )
        self._calibrate_btn.setObjectName("calibrateBtn")
        self._calibrate_btn.setToolTip("Calibrate scale")
        self._calibrate_btn.clicked.connect(self._open_calibration)
        layout.addWidget(self._calibrate_btn)

        self._theme_toggle_btn = Button(
            icon_name="moon" if self._theme_mode == "dark" else "sun",
            variant="ghost",
            size="md",
            parent=bar,
        )
        self._theme_toggle_btn.setObjectName("themeToggleBtn")
        self._theme_toggle_btn.setToolTip("Toggle light / dark theme")
        self._theme_toggle_btn.clicked.connect(self._on_toggle_theme)
        layout.addWidget(self._theme_toggle_btn)

        self._user_avatar = Pill(text="Z", variant="neutral", parent=bar)
        self._user_avatar.setFixedSize(32, 32)
        self._user_avatar.setObjectName("userAvatar")
        layout.addWidget(self._user_avatar)

        return bar

    # --- Nav rail -----------------------------------------------------

    def _build_nav_rail(self) -> QFrame:
        rail = QFrame(self)
        rail.setObjectName("navRail")
        rail.setProperty("surface", "1")
        rail.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rail.setFixedWidth(NAV_RAIL_WIDTH)
        rail.setStyleSheet(_surface_border_qss("navRail", "1", "right"))

        layout = QVBoxLayout(rail)
        layout.setContentsMargins(
            tokens["space"][1], tokens["space"][3],
            tokens["space"][1], tokens["space"][3],
        )
        layout.setSpacing(tokens["space"][2])

        # Icon names are restricted to ``ui/theme/icons.py`` — ``frame-corners``
        # stands in for the home glyph until phosphor "house" lands.
        items = [
            ("frame-corners", "Home", "home"),
            ("upload", "Extraction", "extraction"),
            ("paint-brush", "Assemblies", "assemblies"),
            ("chat-circle", "Chat", "chat"),
            ("gear", "Settings", "settings"),
        ]
        self._nav_buttons: dict[str, Button] = {}
        for icon_name, tooltip, key in items:
            btn = Button(icon_name=icon_name, variant="ghost", size="md", parent=rail)
            btn.setToolTip(tooltip)
            btn.setObjectName(f"navBtn_{key}")
            self._nav_buttons[key] = btn
            layout.addWidget(btn)
        self._nav_buttons["extraction"].setProperty("active", True)
        layout.addStretch(1)
        return rail

    # --- Sheet rail (delegated) ---------------------------------------

    def _build_sheet_rail(self) -> SheetRail:
        cache_dir = self._config.get("cache_dir", "./cache")
        rail = SheetRail(parent=self, cache_dir=cache_dir)
        rail.setObjectName("sheetRail")
        return rail

    # --- Workspace host ----------------------------------------------

    def _build_workspace_host(self) -> QTabWidget:
        tabs = QTabWidget(self)
        tabs.setObjectName("workspaceHost")
        tabs.setDocumentMode(True)

        self._takeoff = TakeoffWorkspace(parent=tabs)
        tabs.addTab(self._takeoff, "Takeoff")

        # Wave 5 commit 7 — DiffWorkspace replaces the disabled placeholder.
        self._diff_workspace = DiffWorkspace(config=self._config, parent=tabs)
        self._diff_workspace.setObjectName("diffWorkspace")
        tabs.addTab(self._diff_workspace, "What Changed")

        # Wave 5 commit 9 — CockpitWorkspace replaces the disabled placeholder.
        cache_dir = self._config.get("cache_dir", "./cache")
        self._cockpit = CockpitWorkspace(parent=tabs, cache_dir=cache_dir)
        self._cockpit.setObjectName("cockpitWorkspace")
        tabs.addTab(self._cockpit, "Cockpit")
        # Surface project meta if present in config.
        project_meta = self._config.get("project_meta", {}) or {}
        if project_meta.get("name"):
            self._cockpit.set_project_name(str(project_meta["name"]))
        if project_meta.get("deadline"):
            self._cockpit.set_deadline(str(project_meta["deadline"]))

        # Wave 6 commit 11 — CoverageWorkspace replaces the disabled placeholder.
        self._coverage = CoverageWorkspace(parent=tabs)
        self._coverage.setObjectName("coverageWorkspace")
        try:
            tabs.addTab(self._coverage, theme_icon("eye"), "Coverage")
        except RuntimeError:
            # qtawesome missing in some test environments — fall back to text-only.
            tabs.addTab(self._coverage, "Coverage")
        if project_meta.get("name"):
            self._coverage.set_project_name(str(project_meta["name"]))
        return tabs

    # --- Inspector placeholder ---------------------------------------

    def _build_inspector_placeholder(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("inspector")
        frame.setProperty("surface", "1")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        frame.setFixedWidth(INSPECTOR_WIDTH)
        frame.setStyleSheet(_surface_border_qss("inspector", "1", "left"))

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(
            EmptyState(
                icon_name="info",
                title="Inspector",
                body="Per-row drill-down coming in Phase 2.",
                parent=frame,
            )
        )
        return frame

    # --- Dock strip ---------------------------------------------------

    @staticmethod
    def _make_bullet(parent: QWidget) -> QLabel:
        sep = QLabel("•", parent)
        sep.setProperty("textSize", "body-sm")
        return sep

    def _build_dock_strip(self) -> QFrame:
        strip = QFrame(self)
        strip.setObjectName("dockStrip")
        strip.setProperty("surface", "2")
        strip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        strip.setFixedHeight(DOCK_STRIP_HEIGHT)
        strip.setStyleSheet(_surface_border_qss("dockStrip", "2", "top"))

        layout = QHBoxLayout(strip)
        layout.setContentsMargins(
            tokens["space"][4], tokens["space"][2],
            tokens["space"][4], tokens["space"][2],
        )
        layout.setSpacing(tokens["space"][3])

        self._cost_label = QLabel("Cost: $0.0000", strip)
        self._cost_label.setObjectName("dockCostLabel")
        self._cost_label.setProperty("textSize", "body-sm")
        layout.addWidget(self._cost_label)

        layout.addWidget(self._make_bullet(strip))
        self._tokens_label = QLabel("0 tokens", strip)
        self._tokens_label.setObjectName("dockTokensLabel")
        self._tokens_label.setProperty("textSize", "body-sm")
        layout.addWidget(self._tokens_label)
        layout.addWidget(self._make_bullet(strip))

        self._progress_bar = QProgressBar(strip)
        self._progress_bar.setObjectName("dockProgressBar")
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar, 1)

        self._run_btn = Button(
            text="Run Extraction", variant="primary", size="sm", parent=strip,
        )
        self._run_btn.setObjectName("runExtractionBtn")
        self._run_btn.clicked.connect(self._on_run_extraction)
        layout.addWidget(self._run_btn)

        return strip

    # ------------------------------------------------------------------
    # Menus
    # ------------------------------------------------------------------

    def _build_menus(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")

        open_action = QAction("&Open PDF…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open_pdf)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        self._sheet_rail.sheet_clicked.connect(self._on_sheet_clicked)
        self._takeoff.row_jump_requested.connect(self._on_row_jump_requested)
        self._diff_workspace.rerun_requested.connect(self._on_diff_rerun)
        # Global ⌘K / Ctrl+K shortcut for the command palette. Application-
        # scoped so it fires regardless of which child widget has focus.
        self._palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._palette_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._palette_shortcut.activated.connect(self._open_command_palette)

    def _ensure_trace_link(self) -> Optional[TraceLink]:
        """Construct the TraceLink the first time the PDF viewer exists.

        The TakeoffWorkspace builds its PDFViewer lazily on the first
        ``load_pdf`` call, so the controller can't be wired at MainWindow
        init. Calling this from PDF-touching slots is the cleanest hook.
        """
        if self._trace_link is not None:
            return self._trace_link
        viewer = self._takeoff.pdf_viewer
        if viewer is None:
            return None
        self._trace_link = TraceLink(
            table=self._takeoff.data_table,
            pdf_viewer=viewer,
            parent=self,
        )
        if hasattr(viewer, "region_clicked"):
            viewer.region_clicked.connect(self._trace_link.jump_to_row)
        # Wave 6 commit 12 — wire the detail-callout pipeline. Lazy
        # detection per page on page_changed (no upfront cost on PDF
        # load); jump signal routes through the same handler row jumps use.
        if hasattr(viewer, "detail_jump_requested"):
            viewer.detail_jump_requested.connect(self._on_detail_jump_requested)
        if hasattr(viewer, "page_changed"):
            viewer.page_changed.connect(self._on_pdf_page_changed)
        return self._trace_link

    def _on_pdf_page_changed(self, page_num: int) -> None:
        """Run callout detection lazily for the page the user just opened."""
        if page_num <= 0:
            return
        viewer = self._takeoff.pdf_viewer
        if viewer is None or not hasattr(viewer, "set_detail_callouts"):
            return
        # Skip if we've already populated this page (cheap dict membership).
        cache = getattr(viewer, "_detail_callouts", None)
        if isinstance(cache, dict) and page_num in cache:
            return
        try:
            from parser.callout_detector import detect_callouts
            doc = getattr(viewer, "_doc", None)
            if doc is None or page_num > doc.page_count:
                return
            page = doc[page_num - 1]
            raw = detect_callouts(page)
        except Exception:
            return
        # Translate PDF-space rects into scene rects + map sheet_id → page.
        sheet_to_page = self._sheet_id_to_page_map()
        try:
            from PyQt6.QtCore import QRectF
        except Exception:
            return
        callouts: list = []
        for rect, text, sheet_id in raw:
            try:
                scene_rect = viewer._pdf_to_scene_rect(rect)
            except Exception:
                scene_rect = QRectF(rect.x0, rect.y0, rect.width, rect.height)
            target = sheet_to_page.get(sheet_id, 0)
            if not target:
                # Try the no-dash variant (e.g. 'A501' → 'A-501').
                target = sheet_to_page.get(sheet_id.replace("-", ""), 0)
            callouts.append((scene_rect, text, int(target)))
        viewer.set_detail_callouts(page_num, callouts)

    def _sheet_id_to_page_map(self) -> dict[str, int]:
        """Build {sheet_number: page_num} from the SheetRail metadata."""
        out: dict[str, int] = {}
        try:
            for sheet_row in getattr(self._sheet_rail, "_rows", []):
                meta = getattr(sheet_row, "meta", None)
                if meta is None:
                    continue
                num = (getattr(meta, "sheet_number", "") or "").strip()
                if not num:
                    continue
                out[num] = int(getattr(meta, "page_num", 0) or 0)
                # Index the no-dash form too, so '4/A501' finds 'A-501'.
                out.setdefault(num.replace("-", ""), int(getattr(meta, "page_num", 0) or 0))
        except Exception:
            pass
        return out

    def _on_detail_jump_requested(self, page_num: int) -> None:
        """Route a callout-bubble click to the same handler used elsewhere."""
        self._on_row_jump_requested(int(page_num), "")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_open_pdf(self) -> None:
        start_dir = str(Path(self._pdf_path).parent) if self._pdf_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", start_dir, "PDF (*.pdf)",
        )
        if not path:
            return
        self._load_pdf(path)

    def _load_pdf(self, path: str) -> None:
        self._pdf_path = path
        self._sheet_rail.load_pdf(path)
        self._takeoff.load_pdf(path)
        # Bind the cockpit to this project so per-PDF state persists
        # across sessions. Row plumbing (cockpit.set_rows) lands when
        # extraction wiring is finalised in a later commit; this commit
        # only wires the fingerprint binding.
        # TODO(commit 11+): call self._cockpit.set_rows(rows) after the
        # extraction worker emits its merged row set.
        try:
            p = Path(path)
            fingerprint = f"{p.name}:{p.stat().st_size}"
            self._cockpit.set_pdf_fingerprint(fingerprint)
        except OSError:
            pass
        # Reset zone cache when the document changes — old zones don't apply.
        self._zone_cache = {}
        self._ensure_trace_link()
        # Wave 6 commit 11 — push current state to coverage / cockpit / etc.
        self._propagate_state()

    def _on_sheet_clicked(self, page_num: int) -> None:
        viewer = self._takeoff.pdf_viewer
        if viewer is not None and hasattr(viewer, "go_to_page"):
            viewer.go_to_page(page_num)
        self._sheet_rail.set_active_sheet(page_num)

    def _on_row_jump_requested(self, page_num: int, _sheet: str) -> None:
        if page_num <= 0:
            return
        viewer = self._takeoff.pdf_viewer
        if viewer is not None and hasattr(viewer, "go_to_page"):
            viewer.go_to_page(page_num)
        self._sheet_rail.set_active_sheet(page_num)

    def _open_command_palette(self) -> None:
        """Build the index from current state and open the palette modal."""
        if self._command_palette is None:
            self._command_palette = CommandPalette(parent=self)
            self._command_palette.item_chosen.connect(self._on_palette_chosen)
        index = self._build_palette_index_for_current_state()
        self._command_palette.set_index(index)
        self._command_palette.open()

    def _build_palette_index_for_current_state(self) -> list[dict]:
        """Snapshot the current workspace into the palette's dict-list."""
        rows: list = []
        try:
            rows = list(self._takeoff.data_table.model().rows())
        except Exception:
            rows = []
        sheet_count = 0
        sheet_titles: dict[int, str] = {}
        try:
            for sheet_row in getattr(self._sheet_rail, "_rows", []):
                meta = getattr(sheet_row, "meta", None)
                if meta is None:
                    continue
                page_num = int(getattr(meta, "page_num", 0) or 0)
                if page_num <= 0:
                    continue
                sheet_count = max(sheet_count, page_num)
                title = getattr(meta, "sheet_number", "") or f"Sheet {page_num}"
                sheet_titles[page_num] = title
        except Exception:
            sheet_count = 0
            sheet_titles = {}
        divisions = sorted({
            (r.trade_division or "").strip()
            for r in rows
            if not r.is_header_row and (r.trade_division or "").strip()
        })
        commands = self._registered_palette_commands()
        return build_palette_index(
            rows=rows,
            sheet_count=sheet_count,
            sheet_titles=sheet_titles,
            divisions=divisions,
            commands=commands,
        )

    def _registered_palette_commands(self) -> list[dict]:
        """Return the static list of palette-invokable commands.

        Each command's ``payload`` is a zero-arg callable. The palette never
        invokes it itself — :py:meth:`_on_palette_chosen` does the calling
        once the user picks a row.
        """
        return [
            {
                "label":    "Toggle theme",
                "subtitle": "Switch between light and dark",
                "payload":  self._on_toggle_theme,
            },
            {
                "label":    "Open PDF…",
                "subtitle": "Pick a drawing set to load",
                "payload":  self._on_open_pdf,
            },
            {
                "label":    "Toggle zone overlay",
                "subtitle": "Show or hide title-block / legend zones",
                "payload":  self._toggle_zone_overlay_via_command,
            },
            {
                "label":    "Compare with…",
                "subtitle": "Diff the loaded PDF against another set",
                "payload":  self._on_compare_with,
            },
            {
                "label":    "Run extraction",
                "subtitle": "Re-run the multi-agent extraction pipeline",
                "payload":  self._on_run_extraction,
            },
            {
                "label":    "Switch to Takeoff tab",
                "subtitle": "Show the PDF + line-item table",
                "payload":  lambda: self._switch_to_workspace(self._takeoff),
            },
            {
                "label":    "Switch to What Changed tab",
                "subtitle": "Compare against another drawing set",
                "payload":  lambda: self._switch_to_workspace(self._diff_workspace),
            },
            {
                "label":    "Switch to Cockpit tab",
                "subtitle": "Bid-day cockpit view",
                "payload":  lambda: self._switch_to_workspace(self._cockpit),
            },
        ]

    def _toggle_zone_overlay_via_command(self) -> None:
        """Flip the zone-overlay button's check state and run its handler."""
        self._zone_overlay_btn.setChecked(not self._zone_overlay_btn.isChecked())
        self._on_toggle_zone_overlay(self._zone_overlay_btn.isChecked())

    def _switch_to_workspace(self, workspace: QWidget) -> None:
        idx = self._workspace_host.indexOf(workspace)
        if idx >= 0 and self._workspace_host.isTabEnabled(idx):
            self._workspace_host.setCurrentIndex(idx)

    def _on_palette_chosen(self, item: dict) -> None:
        """Dispatch the palette's chosen item to the right handler."""
        if not isinstance(item, dict):
            return
        kind = item.get("type")
        payload = item.get("payload")
        if kind == "command" and callable(payload):
            payload()
            return
        if kind == "row" and isinstance(payload, dict):
            page = int(payload.get("page") or 0)
            sheet = str(payload.get("sheet") or "")
            if page > 0:
                self._on_row_jump_requested(page, sheet)
            return
        if kind == "sheet":
            try:
                page = int(payload)
            except (TypeError, ValueError):
                return
            if page > 0:
                self._on_sheet_clicked(page)
            return
        if kind == "division" and isinstance(payload, str) and payload:
            try:
                self._takeoff.data_table.filter_trade(payload)
                self._switch_to_workspace(self._takeoff)
            except Exception:
                Toaster.show(f"Could not filter by {payload}", variant="warning")
            return

    def _on_toggle_theme(self) -> None:
        self._theme_mode = "light" if self._theme_mode == "dark" else "dark"
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode=self._theme_mode)

    def _on_run_extraction(self) -> None:
        Toaster.show(
            "Run Extraction not wired in this commit — full wiring in commit 4+.",
            variant="warning",
        )

    def _on_compare_with(self) -> None:
        """Open a file picker, then hand the chosen PDF to the diff tab."""
        if not self._pdf_path:
            Toaster.show("Load a PDF first.", variant="warning")
            return
        start_dir = str(Path(self._pdf_path).parent)
        path, _ = QFileDialog.getOpenFileName(
            self, "Compare with…", start_dir, "PDF (*.pdf)",
        )
        if not path:
            return
        # Surface the workspace tab and start the diff in one motion.
        idx = self._workspace_host.indexOf(self._diff_workspace)
        if idx >= 0:
            self._workspace_host.setCurrentIndex(idx)
        self._diff_workspace.open_compare(self._pdf_path, path)

    def _on_diff_rerun(self, _result: object) -> None:
        """Stub — the ExtractionWorker hookup lands in a later commit."""
        Toaster.show(
            "Re-extract scheduled — wires to ExtractionWorker in commit 11+.",
            variant="info",
        )

    def _open_calibration(self) -> None:
        """Open the per-sheet scale calibration dialog modally."""
        sheets = self._sheet_ids_for_calibration()
        cache_dir = self._config.get("cache_dir", "./cache")
        fingerprint = ""
        if self._pdf_path:
            try:
                p = Path(self._pdf_path)
                fingerprint = f"{p.name}:{p.stat().st_size}"
            except OSError:
                fingerprint = ""
        dialog = CalibrationDialog(
            sheets=sheets,
            cache_dir=cache_dir,
            pdf_fingerprint=fingerprint,
            parent=self,
        )
        dialog.calibration_applied.connect(self._on_calibration_applied)
        dialog.exec()

    def _sheet_ids_for_calibration(self) -> list[str]:
        """Return the sheet-number list from the SheetRail, in page order."""
        out: list[str] = []
        try:
            for sheet_row in getattr(self._sheet_rail, "_rows", []):
                meta = getattr(sheet_row, "meta", None)
                if meta is None:
                    continue
                sheet_number = (getattr(meta, "sheet_number", "") or "").strip()
                if sheet_number:
                    out.append(sheet_number)
        except Exception:
            return []
        return out

    def _on_calibration_applied(
        self, sheets: list, scale: float, units: str,
    ) -> None:
        """Stub handler — fires a Toast confirming calibration save.

        Wiring the scale into the extraction pipeline is beyond the
        scope of this commit. Calibration just persists to JSON for now.
        """
        # ``scale`` and ``units`` are part of the public signal contract
        # but the stub only needs the affected-sheet count for the toast.
        del scale, units
        n = len(sheets) if sheets else 0
        Toaster.show(
            f"Calibration saved · scale propagated to {n} sheet"
            + ("s" if n != 1 else ""),
            variant="info",
        )

    def _propagate_state(self) -> None:
        """Fan out the current row + sheet state to every interested panel.

        Called after the extraction worker emits and after a PDF load
        produces page classifications. The cockpit and coverage panels
        both want the row set; coverage additionally wants the sheet
        classifications. Other panels can hook in here as they land.
        """
        rows: list = []
        try:
            rows = list(self._takeoff.data_table.model().rows())
        except Exception:
            rows = []
        if hasattr(self, "_cockpit") and self._cockpit is not None:
            try:
                self._cockpit.set_rows(rows)
            except Exception:
                pass
        if hasattr(self, "_coverage") and self._coverage is not None:
            try:
                self._coverage.set_rows(rows)
                self._coverage.set_sheets(self._page_classifications())
            except Exception:
                pass

    def _page_classifications(self) -> dict[int, dict]:
        """Return ``{page_num: {"page_type", "sheet_id", "skip"}}``.

        Built from SheetRail metadata; ``page_type`` and ``skip`` come
        from the parser when available, otherwise default to neutral
        values that keep the coverage rendering tolerant.
        """
        out: dict[int, dict] = {}
        try:
            rows = getattr(self._sheet_rail, "_rows", []) or []
        except Exception:
            return out
        for sheet_row in rows:
            meta = getattr(sheet_row, "meta", None)
            if meta is None:
                continue
            page_num = int(getattr(meta, "page_num", 0) or 0)
            if page_num <= 0:
                continue
            out[page_num] = {
                "sheet_id": getattr(meta, "sheet_number", "") or f"Page {page_num}",
                "page_type": getattr(meta, "page_type", "") or "",
                "skip": bool(getattr(meta, "skip", False)),
            }
        return out

    def _on_toggle_zone_overlay(self, checked: bool) -> None:
        """Toggle the per-page zone overlay; segments lazily and caches results."""
        viewer = self._takeoff.pdf_viewer
        if viewer is None or not hasattr(viewer, "show_zone_overlay"):
            Toaster.show("Load a PDF before toggling zones.", variant="warning")
            self._zone_overlay_btn.setChecked(False)
            return
        if not checked:
            viewer.hide_zone_overlay()
            return
        page_num = int(getattr(viewer, "current_page", 0) or 0)
        if page_num <= 0:
            self._zone_overlay_btn.setChecked(False)
            return
        zones = self._zone_cache.get(page_num)
        if zones is None:
            try:
                # Imported lazily — segmenter pulls in OpenCV / numpy which we
                # don't want to load before the toggle is first used.
                from parser.zone_segmenter import segment as _segment
                import fitz as _fitz
                if not viewer.pdf_path:
                    raise RuntimeError("no pdf path")
                with _fitz.open(viewer.pdf_path) as doc:
                    zones = _segment(doc[page_num - 1], page_num=page_num)
                self._zone_cache[page_num] = zones
            except Exception as exc:  # pragma: no cover — defensive
                Toaster.show(f"Zone segmentation failed: {exc}", variant="danger")
                self._zone_overlay_btn.setChecked(False)
                return
        viewer.show_zone_overlay(page_num, zones)


__all__ = ["MainWindow", "TOPBAR_HEIGHT", "NAV_RAIL_WIDTH", "DOCK_STRIP_HEIGHT"]
