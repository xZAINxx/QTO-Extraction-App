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
from PyQt6.QtGui import QAction
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
from ui.controllers.trace_link import TraceLink
from ui.panels.sheet_rail import SheetRail
from ui.theme import apply_theme, tokens
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
        self._cmd_palette_btn.clicked.connect(self._on_command_palette)
        layout.addWidget(self._cmd_palette_btn)

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

        # Placeholder tabs — disabled until commits 7 / 9 / 11 land them.
        placeholders = [
            ("Diff", "git-diff", "Compare two PDF sets — coming in commit 7."),
            ("Cockpit", "frame-corners", "Bid-day cockpit — coming in commit 9."),
            ("Coverage", "info", "Coverage / holes report — coming in commit 11."),
        ]
        for title, icon_name, body in placeholders:
            ph = EmptyState(icon_name=icon_name, title=title, body=body, parent=tabs)
            idx = tabs.addTab(ph, f"{title} (coming soon)")
            tabs.setTabEnabled(idx, False)
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
        return self._trace_link

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
        # Reset zone cache when the document changes — old zones don't apply.
        self._zone_cache = {}
        self._ensure_trace_link()

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

    def _on_command_palette(self) -> None:
        Toaster.show("Command palette ⌘K — coming in commit 8", variant="info")

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
