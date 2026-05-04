# PyQt6 UI Redesign — Modern SaaS Reimagination

## Context

The Zeconic QTO tool's current PyQt6 UI works but reads as a 2018-era developer dashboard: AI-blue accent on near-black background, Unicode glyphs as icons (`○ ◉ ✓ ⊘ ✗`), system-default fonts, hardcoded spacing, and a single 248px sidebar that crams upload form, stats card, tool buttons, assembly tree, and primary actions into one cramped column. After committing the multi-agent extraction refactor (6 commits, 141 tests green), I want to reimagine the front end with a SaaS-grade aesthetic and add the workflow features a 25-year construction estimator would actually brag about.

**Decided constraints (confirmed with user via AskUserQuestion):**
- Implementation tech: **PyQt6 in place**. Not a web rewrite. Native desktop kept.
- Scope: **Full reimagination** — both visuals and features.
- Reference SaaS aesthetics from STACK CT, Togal AI, Kreo (without copying any one of them — they each have their own clichés).
- Use design principles from `/Users/zain/.agents/skills/design-taste-frontend/SKILL.md` as a baseline (DESIGN_VARIANCE 8, MOTION_INTENSITY 6, VISUAL_DENSITY 4).
- All current functionality must keep working: multi-agent extraction, Phase 7 cost-saver batch, RAG store, set-diff, chat, pattern search, assemblies, Excel export.

## Mental-Model Corrections

The skill file is React/Tailwind-oriented; we translate its principles into PyQt6:
- **Tailwind utility classes → QSS** generated from a token dict.
- **Phosphor React icons → qtawesome** ≥ 1.3 (which bundles the Phosphor pack as `ph.*`).
- **Framer Motion → QPropertyAnimation** wrapped in a single `Animator` helper.
- **`@apply` style composition → QSS attribute selectors driven by `widget.setProperty("variant", "primary")`** dynamic properties (no `polish()`/`unpolish()` jank).
- **`'use client'` interactivity isolation → QDockWidget**, which gives multi-monitor support natively.

Two facts the brief named that I want to flag:
- **Brand vs. data colors are different namespaces.** The "max 1 accent" rule governs interactive chrome (buttons, focus rings, links). Construction's domain semantics — yellow=confirmed, pink=revision, green=approved, red=demo — are separate data tokens. Both palettes coexist; we never cross them.
- **`QTableWidget` cannot virtualize.** The 10k-row scroll target requires migrating to `QTableView` + `QAbstractTableModel`. Non-trivial — budget it explicitly.

## Design Decisions

### 1. Color tokens — semantic, dual-mode, green-accent (industry convention)

Replace the current CANVAS/SURFACE_*/INDIGO palette with a calibrated stone-base + emerald-accent system. **All three SaaS QTO references converge on green as the primary action color** (STACK #33B270, Togal #3AB65A, Kreo #29AD67) — green is the industry's universal "measurement confirmed, money saved" signal. It also escapes the design-taste skill's "AI neon blue" ban.

Both modes share token names; only hex values switch.

```
                              DARK            LIGHT
color.bg.canvas               #0B0D10         #FAFAF9
color.bg.surface.1            #14171C         #FFFFFF
color.bg.surface.2            #1B1F26         #F4F4F2
color.bg.surface.3            #232830         #ECECEA
color.bg.surface.raised       #2A3038         #FFFFFF (+shadow)
color.border.subtle           #232830         #E7E5E4
color.border.default          #2D333D         #D6D3D1
color.border.strong           #3F4651         #A8A29E
color.text.primary            #F5F5F4         #1C1917
color.text.secondary          #A8A29E         #57534E
color.text.tertiary           #78716C         #78716C

# Brand — single accent, industry-standard emerald, ~64% saturation
color.accent.default          #33B270         #16A34A
color.accent.hover            #4BC588         #15803D
color.accent.pressed          #259458         #166534
color.accent.subtle           #0E2818         #ECFAF2
color.accent.on               #FFFFFF         #FFFFFF

# UI states — restrained
color.success                 #16A34A         #15803D   # may alias accent in some contexts
color.warning                 #D97706         #B45309   # amber — AI-in-progress, soft warnings
color.danger                  #DC2626         #B91C1C
color.info                    #475569         #334155   # slate, NOT blue

# Domain semantics — construction estimator conventions (orthogonal to brand)
color.confirmed-yellow        #FACC15         #FACC15   # universal "I counted this" highlight
color.revision-pink           #EC4899         #DB2777
color.demo-red                #DC2626         #B91C1C
color.approved-green          #16A34A         #15803D
color.mep-blue                #0EA5E9         #0284C7
color.scope-out               #78716C         #A8A29E
```

**Tri-color logic to codify in `tokens.py` docstring:** brand emerald drives interactive chrome (buttons, focus rings, links). Amber `color.warning` drives transient AI-in-progress states and soft warnings. Domain semantics (yellow/pink/green/red/blue) drive *data state* on rows. These three palettes never cross — never use brand emerald for data state, never use yellow for interactive chrome.

### 2. Typography — Geist Sans + Geist Mono, bundled

- Bundle Geist Sans + Geist Mono in `assets/fonts/Geist/` (SIL Open Font License — confirmed safe for embedding).
- Load once at app boot via `QFontDatabase.addApplicationFont`. Fall through to `system-ui` only if loading fails (log warning).
- Tabular figures via `QFont.setFeatures({"tnum": 1})` on all numeric columns and metric values.
- Ban `.AppleSystemUIFont` and `Inter` from QSS.

Type scale (px / line-height / weight):

```
caption      11 / 16 / 500    UPPERCASE +0.06em — column headers, metric labels
body-sm      12 / 18 / 400    table rows, helper text
body         13 / 20 / 400    default UI
body-lg      14 / 22 / 400    inspector, descriptions
h6           14 / 20 / 600    inline section
h5           16 / 24 / 600    panel titles
h4           18 / 26 / 600    modal titles, view headers
h3           22 / 30 / 600    topbar project name, cockpit metrics
h2           28 / 36 / 600    cockpit total
h1           36 / 44 / 600    empty-state hero only
mono-sm      12 / 18 / 400    tabular numerics
mono         13 / 20 / 400    math_trail, tags
```

### 3. Spacing / radius / shadow / motion / icon tokens

```
space.0  0    space.1  4    space.2  8    space.3  12
space.4  16   space.5  20   space.6  24   space.8  32
space.12 48   space.16 64

radius:  0 / 4 / 6 / 8 / 12 / 16 / full(9999)

shadow.1 = 0 1 2 rgba(0,0,0,0.20)    # cards
shadow.2 = 0 4 8 rgba(0,0,0,0.30)    # popovers
shadow.3 = 0 12 24 rgba(0,0,0,0.40)  # modals, command palette
shadow.4 = 0 24 48 rgba(0,0,0,0.50)  # detached windows highlight

motion.fast    120ms cubic-bezier(0.4,0,0.2,1)
motion.normal  200ms cubic-bezier(0.4,0,0.2,1)
motion.slow    320ms cubic-bezier(0.4,0,0.2,1)
motion.spring  400ms cubic-bezier(0.34,1.56,0.64,1)
```

Light-mode shadows reduce to 0.06–0.20 alpha. All animation calls go through one `Animator` helper.

**Icons:** `qtawesome>=1.3` (bundles Phosphor). Wrapped in `ui/theme/icons.py` so callers say `icon("upload")`, not `qta.icon("ph.upload")`. Phosphor (line weight 1.5) for almost everything; fall back to MaterialDesignIcons for the few construction-specific glyphs Phosphor lacks.

The 30 Phosphor icons we'll need: `upload`, `play`, `stop-circle`, `pause`, `download-simple`, `magnifying-glass`, `funnel`, `eye`, `check-circle`, `warning`, `x-circle`, `arrows-clockwise`, `caret-left`, `caret-right`, `caret-down`, `dots-three`, `command`, `chat-circle`, `gear`, `sun`, `moon`, `corners-out` (detach), `frame-corners` (cockpit), `git-diff`, `compass-tool` (calibration), `paint-brush` (assemblies), `tag`, `lightbulb` (AI suggestion), `info`, `floppy-disk` (export).

### 4. Layout architecture — new IA

Current: 248px Sidebar (UploadPanel + StatsBar + tool buttons + AssemblyPalette + actions) + Splitter[PDFViewer | ResultsTable+ProgressPanel] + 32px CostMeter footer.

New: topbar + nav rail + sheet rail + workspace tabs + inspector + dock strip. Each column has one job.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TOPBAR  56px                                                                │
│ [logo] Project switcher ▾ │ Mode: Hybrid │ ⌘K Search ── │ ☼ │ user         │
├──────┬─────────────────┬──────────────────────────────┬─────────────────────┤
│ NAV  │ SHEET RAIL      │  WORKSPACE                   │ INSPECTOR           │
│ RAIL │ 220px           │  flex                        │ 320px (collapsible) │
│ 56px │ collapse → 64   │                              │                     │
│      │                 │  Tabs: Takeoff │ Diff │      │ Per-row details:    │
│ icon │ thumbnails      │        Cockpit │ Coverage   │  - Source provenance│
│ only │ + sheet meta    │                              │  - Confidence break │
│      │ disciplines     │  ┌──── canvas ────┬─table──┐ │  - Risk flags       │
│ ◐    │ revision tags   │  │ PDF + overlay  │ rows   │ │  - Edit history     │
│      │ scope status    │  │                │        │ │  - Linked sheet     │
│ home │ search + filter │  │ trace-back     │ filter │ │                     │
│ ext  │                 │  │ highlight on   │ bar    │ │                     │
│ asm  │                 │  │ row click      │ ↕      │ │                     │
│ chat │                 │  │                │        │ │                     │
│ ...  │                 │  │ ⤢ detach       │ ⤢      │ │                     │
│      │                 │  └────────────────┴────────┘ │                     │
├──────┴─────────────────┴──────────────────────────────┴─────────────────────┤
│ DOCK STRIP  44px (collapsible)                                              │
│ Cost $0.42 · 1.2k tok · cache 94% │ Phase 7: 12/40 │ Progress ▒▒▒░░ 60% │  │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **Topbar (56px)**: project switcher, extraction-mode badge (clickable → settings popover), command palette trigger, theme toggle.
- **Nav rail (56px, icon-only)**: Home, Extraction, Assemblies, Chat, Settings. Tooltip on hover.
- **Sheet rail (220px, collapsible to 64)**: vertical thumbnails, each with sheet number, discipline letter, revision pill, scope status dot. Top: search + discipline filter chips.
- **Workspace center (flex, tabbed)**: Takeoff | What Changed | Cockpit | Coverage. Inside Takeoff: PDF canvas + DataTable side-by-side.
- **Inspector (320px, collapsible)**: per-row drill-down — provenance, confidence breakdown, risk flags, edit history.
- **Dock strip (44px)**: cost meter + active progress mini-readout.
- **Detached panels**: PDF viewer, chat, inspector, sheet rail each as `QDockWidget` with `setFloating(True)` available — multi-monitor for free. State persisted via `saveState()`/`restoreState()`.

### 5. Phase 1 features — top 8 ranked by impact × shippability

| # | Feature | Difficulty | Notes |
|---|---|---|---|
| 1 | Trace-back overlay (row ↔ region sync) | M | Extends `QTORow` with `bbox: tuple[float,float,float,float] | None`. Bidirectional via `controllers/trace_link.py`. |
| 2 | Sheet index rail with thumbnails | M | New `SheetRail` widget. Thumbnails cached via `QPixmapCache`. Scope status persisted to `cache/scope.json`. |
| 3 | StatusPill per row (confidence + next action) | S | Color-coded: ≥0.9 green Confirm, 0.6–0.9 amber Review, <0.6 red Re-extract. Click pill → action. |
| 4 | Yellow-confirm flow | S | Y key or pill click → row painted `confirmed-yellow` (light-tinted in dark mode). Persisted via `ResultCache`. Adds `confirmed: bool` to `QTORow`. |
| 5 | What-Changed workspace tab | M | Promote `set_diff_view.py` from modal to first-class tab with $-impact column. |
| 6 | Command palette ⌘K | M | Frameless dialog, fuzzy search rows/sheets/divisions/commands. `rapidfuzz` or stdlib `difflib`. |
| 7 | Detached panels (`QDockWidget`) | S | Multi-monitor via native Qt. State persisted to `~/.qto_tool/window_state.bin`. |
| 8 | Cockpit mode workspace | M | Bid-day view: total, division chart, sub-bid table, markup sliders, exclusions, deadline clock, regenerate-proposal button (export stub in P1). |

### 6. Phase 2 features — 4

9. Drag-and-drop reclassify between divisions (preserves provenance).
10. Coverage / "holes" workspace — spec sections with zero line items + division benchmarking.
11. Risk flag pills per row (spec ambiguity, design-development drawing, volatile material).
12. Detail-bubble preview — hover `4/A-501` callout in canvas → tooltip with detail thumbnail; click → jump to sheet.

### 7. Phase 3 features — 2 (lower-priority polish + final flag flip)

13. Calibration manager — per-sheet scale calibration with propagate-to-series.
14. Detail-bubble preview shipped + flag flip default to `ui_v2: true` + delete `ui/legacy/`.

**Deferred (not in scope):** Excel round-trip file watcher (XL effort), inline RAG ghost-text suggestions during cell edit (XL effort, requires per-keystroke embedding calls), in-app formula cells in DataTable (Kreo's "live spreadsheet" pattern — XL effort, needs a formula engine). All can land as follow-up tickets after the redesign settles.

**Underexploited differentiator surfaced by competitor research:** STACK / Togal / Kreo are all cloud-only. This tool is **local-first / desktop-first** — a major advantage for data-sensitive government, defense, and healthcare construction projects where drawing sets cannot leave the network. The redesign should subtly market this fact in onboarding (e.g., the empty-state copy on first launch: "Your drawings stay on this machine. No cloud uploads."). No engineering work — copy decision only.

**Drawing-zone classification already exists** in [parser/zone_segmenter.py](parser/zone_segmenter.py) — the existing pipeline tags title block, legend, schedule, and main drawing zones. Kreo brags about exactly this as "Caddie AI zone mapping." We should surface the existing zones visibly on the PDF canvas (faint colored bounding boxes on hover, toggled via a canvas overlay menu). Cheap win — zero new extraction logic, just a viewer overlay layer in `views/pdf_canvas.py`. Add this as part of commit 6 (trace-back overlay) since they share the overlay infrastructure.

### 8. Component library — `ui/components/`

| Component | Variants | PyQt6 approach |
|---|---|---|
| `Button` | primary/secondary/ghost/danger × sm/md/lg, icon-only | `QPushButton` subclass + dynamic property `variant`/`size` styled via QSS |
| `Pill` / `Badge` | info/success/warning/danger/neutral, with-dot | `QLabel` subclass with `paintEvent` for dot |
| `StatusPill` | confidence + next-action combo | Composite widget |
| `Card` | elevated/flat, with-header | `QFrame` subclass; shadow via `QGraphicsDropShadowEffect` |
| `Input` | text/number/select/multi-select/search | Styled `QLineEdit`/`QComboBox`; floating label via overlay |
| `Toggle` | sm/md | `QCheckBox` subclass with custom `paintEvent` |
| `Tooltip` | rich content | `QFrame` popup driven by event filter |
| `Tabs` | line / pill / segmented | `QTabBar` subclass |
| `DataTable` | sort/filter/multi-select/virtualized | **`QTableView` + `QAbstractTableModel`** (replaces `QTableWidget` — required for 10k row target) |
| `Skeleton` | line/block/table-row | `QWidget` with `QPropertyAnimation` shimmer |
| `EmptyState` | icon + title + body + CTA | Composite widget |
| `CommandPalette` | — | Frameless `QDialog` |
| `DrawerPanel` | left/right, collapsible | `QDockWidget` subclass with collapse-to-rail mode |
| `Toast` | info/success/warning/danger, autohide | `QWidget` overlay queued from `Toaster` singleton |
| `Modal` | sm/md/lg/fullscreen | `QDialog` subclass with size token enforcement |
| `Thumbnail` | with metadata | Composite (QLabel pixmap + meta pills) |

## New `ui/` Directory Structure

```
ui/
  __init__.py
  theme/
    tokens.py            # all design tokens, dark + light dicts
    qss.py               # generates QSS from active token set
    fonts.py             # QFontDatabase loader (Geist Sans + Mono)
    icons.py             # qtawesome wrapper
    motion.py            # Animator helper for QPropertyAnimation
  components/
    button.py
    pill.py
    status_pill.py
    card.py
    input.py
    toggle.py
    tabs.py
    data_table.py        # QTableView + model, virtualized
    skeleton.py
    empty_state.py
    toast.py
    command_palette.py
    drawer_panel.py
    split_pane.py
    thumbnail.py
    modal.py
  panels/
    sheet_rail.py        # NEW — left thumbnails
    inspector.py         # NEW — right per-row inspector
    cost_dock.py         # was cost_meter.py
    progress_dock.py     # was progress_panel.py
    chat_dock.py         # was chat_panel.py
    assembly_dock.py     # was assembly_palette.py
    upload_dialog.py     # was upload_panel.py — promoted to project-create dialog
  workspaces/
    takeoff_workspace.py # PDF + DataTable + filter bar
    diff_workspace.py    # was set_diff_view.py
    cockpit_workspace.py # NEW
    coverage_workspace.py# NEW (Phase 2)
  views/
    main_window.py       # rewritten — topbar + nav rail + workspace host
    pdf_canvas.py        # was pdf_viewer.py + trace-back overlay
  controllers/
    trace_link.py        # NEW — row ↔ region binding
    extraction_worker.py # extracted from old main_window.py (no logic change)
  legacy/                # all current ui/*.py copied here for reference, deleted in final commit
```

### Migration table — what changes vs. what's preserved

| Current file | Fate |
|---|---|
| [ui/theme.py](ui/theme.py) | **Replaced** by `theme/` package. Same hex constants exported as deprecated re-exports for one release. |
| [ui/main_window.py](ui/main_window.py) | **Rewritten** as `views/main_window.py`. `ExtractionWorker` extracted to `controllers/extraction_worker.py` unchanged. |
| [ui/results_table.py](ui/results_table.py) | **Rewritten** as `components/data_table.py` + `workspaces/takeoff_workspace.py`. `QTableWidget` → `QTableView` + model. |
| [ui/pdf_viewer.py](ui/pdf_viewer.py) | **Renamed + extended** as `views/pdf_canvas.py`. Adds trace-back overlay layer. |
| [ui/progress_panel.py](ui/progress_panel.py) | **Moved** to `panels/progress_dock.py`. Becomes a `QDockWidget`. Logic preserved. |
| [ui/cost_meter.py](ui/cost_meter.py) | **Moved** to `panels/cost_dock.py`. Becomes a `QDockWidget`. |
| [ui/stats_bar.py](ui/stats_bar.py) | **Absorbed** into `panels/cost_dock.py` + `views/main_window.py` topbar. |
| [ui/upload_panel.py](ui/upload_panel.py) | **Promoted** to `panels/upload_dialog.py` — opens as a modal on Project → New. Form labels move ABOVE inputs. |
| [ui/chat_panel.py](ui/chat_panel.py) | **Moved** to `panels/chat_dock.py`. Becomes a `QDockWidget`. |
| [ui/assembly_palette.py](ui/assembly_palette.py) | **Moved** to `panels/assembly_dock.py`. |
| [ui/set_diff_view.py](ui/set_diff_view.py) | **Promoted** to `workspaces/diff_workspace.py` — workspace tab, not modal. Adds $-impact column. |
| [ui/pattern_search_dialog.py](ui/pattern_search_dialog.py) | **Kept as modal** — unchanged in P1, restyled to new tokens. |

## Files To Modify Outside `ui/`

| File | Change |
|---|---|
| [requirements.txt](requirements.txt) | Add `qtawesome>=1.3.0`, `rapidfuzz>=3.0` |
| [config.yaml](config.yaml) | Add `ui_v2: false` flag (default off until parity reached) |
| [core/qto_row.py](core/qto_row.py) | Extend dataclass: `bbox: tuple[float,float,float,float] | None = None`, `confirmed: bool = False`. Backward-compatible — defaults preserve current behavior. |
| [main.py](main.py) | Branch on `config["ui_v2"]` to import `ui.views.main_window` or `ui.legacy.main_window`. |
| `assets/fonts/Geist/` | NEW — bundle Geist Sans + Geist Mono `.ttf` files + `LICENSE.txt` (SIL OFL 1.1). |

## Files To Reuse Without Modification

- All `ai/`, `core/` (except `qto_row.py`), `parser/`, `cv/` modules — UI redesign does not touch the extraction pipeline.
- `tests/` — all 141 existing tests target `ai/`, `core/`, `parser/`, `cv/`. They keep passing automatically since they don't import from `ui/`.
- [ESTIMATE_FORMAT___GC.xlsx](ESTIMATE_FORMAT___GC.xlsx) — Excel export template unchanged.

## Migration Strategy — Progressive Behind a Feature Flag

**Strategy: NOT a big-bang rewrite. Component-by-component behind `ui_v2: bool` flag.**

1. Add `ui_v2: false` to `config.yaml`.
2. `main.py` picks `ui.views.main_window` when true, else `ui.legacy.main_window`. Both work in parallel.
3. Each commit lands a coherent slice that runs end-to-end on `ui_v2=true`, even if some panels still use legacy widgets adapted via thin shims.
4. Final commit (12): flip default to `true`, delete `ui/legacy/`.

This means at any commit on the branch, you can run the app on either UI.

### Smoothness — concrete techniques

- **One-shot QSS generation** — `build_stylesheet(tokens)` runs once on theme change, applied to `QApplication`. Never set per-widget styles in code.
- **Dynamic properties + attribute selectors** — `widget.setProperty("variant", "primary")` with `QPushButton[variant="primary"] { ... }`. No `polish()`/`unpolish()` jank when properties change.
- **Lazy widget construction** — `Inspector`, `ChatDock`, `CockpitWorkspace` build their widget tree on first `show()`, not at MainWindow init.
- **Animator-cap** — all `QPropertyAnimation` calls go through one `Animator` helper, capped at 200ms, no overlapping animations on the same widget.
- **PDF render budget** — single global `QPixmapCache` of 256 MB shared by sheet-rail thumbnails and full-page renders. Thumbnails generated on-demand at first scroll on a `QThreadPool` worker, never on the GUI thread.
- **Trace-back debounce** — single `QTimer` per overlay layer to coalesce highlight pulses; no `update()` storms.

### Performance targets

- DataTable scroll over 10k synthetic rows holds 60fps.
- PDF canvas first-paint of a 500-page PDF under 800ms (fitz lazy-loads pages).
- Theme toggle latency under 150ms on a 2k-row table.

## Commit Sequence — 12 commits across 3 phases

**Phase 1 (commits 1–6) — biggest visual delta, end-to-end on `ui_v2=true`:**

1. **`feat(ui): tokens, QSS generator, light/dark toggle, Geist fonts, qtawesome wired`** — `ui/theme/` package. Old UI keeps running with new tokens applied to legacy widgets.
2. **`feat(ui): component library skeleton`** — Button, Pill, StatusPill, Card, EmptyState, Skeleton, Toast in `ui/components/`. Smoke test per component (`tests/test_components_smoke.py`).
3. **`feat(ui): layout shell`** — topbar + nav rail + workspace host + dock strip + QDockWidget plumbing. Legacy panels adapted as dock contents. `ui_v2` flag lit.
4. **`feat(ui): SheetRail panel`** — thumbnails + scope-status persistence to `cache/scope.json`.
5. **`feat(ui): DataTable migration`** — `QTableView` + `QAbstractTableModel` + StatusPill column + confidence pills + yellow-confirm flow. Adds `confirmed: bool` to `QTORow`.
6. **`feat(ui): trace-back overlay`** — `TraceLink` controller + `bbox` on `QTORow` + canvas highlight + table jump.

**Phase 2 (commits 7–10):**

7. **`feat(ui): What-Changed workspace tab`** — promote `set_diff_view.py`, add $-impact column.
8. **`feat(ui): command palette ⌘K`** — global shortcut, fuzzy index over rows/sheets/divisions/commands.
9. **`feat(ui): cockpit workspace`** — division chart, markup sliders, exclusions, deadline.
10. **`feat(ui): drag-and-drop reclassify + risk flag pills`**.

**Phase 3 (commits 11–12):**

11. **`feat(ui): coverage workspace + calibration manager`**.
12. **`feat(ui): detail bubble preview, flip ui_v2 default to true, delete ui/legacy/`**.

Time estimate: ~12 working days. Phase 1 is ~6 days and lands the bulk of the visual transformation. Phase 1 alone is shippable as "v2 beta".

## Verification

**Visual regression** — golden screenshots at fixed states (empty workspace, mid-extraction with 3 pages done, 200-row takeoff loaded, cockpit with sample data, diff tab open). Captured via `QWidget.grab()` in headless `pytest-qt` test, compared with small SSIM tolerance. Six screenshots × 2 themes = 12 fixtures.

**Manual flow checklist** (each phase):
- Load PDF → sheet rail populates → click sheet → canvas jumps.
- Click row → canvas highlights bbox; click bbox → row scrolls + highlights.
- Confirm row (Y key) → row turns yellow → persists across reload.
- ⌘K → type sheet number → enter → canvas jumps.
- Detach PDF dock → drag to second monitor → resize → reattach → state persists across restart.
- Toggle theme → all components re-render without ghosting.
- Open Compare → diff tab opens → re-extract → rows merge.

**Performance benchmarks** (`tests/test_perf.py`):
- DataTable scroll over 10k synthetic rows holds 60fps.
- PDF canvas first-paint of 500-page PDF under 800ms.
- Theme toggle under 150ms on 2k-row table.

**Existing test suite** — 141 tests pass automatically (none touch `ui/`).

**Multi-monitor** — manual only; document steps in PR.

## Risks (Flagged Explicitly)

1. **`QTableWidget` → `QTableView` migration is non-trivial.** Editable cells, context menu, filter bar, save-as-assembly hookups all replumb against the model layer. Budget 1.5 days, not 0.5. If we don't do this, the 10k-row claim is fiction. Commit 5 is the riskiest single commit.
2. **Geist license** — Vercel ships Geist under SIL OFL 1.1; safe for embedding. Include `assets/fonts/Geist/LICENSE.txt` in the commit. No legal risk.
3. **Light mode on PDF canvas** — PyMuPDF page renders are RGB bitmaps regardless of theme. The canvas background stays near-white in both modes (no inversion). Trace-back overlay color uses `confirmed-yellow` at 35% alpha for highlight, not brand amber, since amber is hard to see on white.
4. **`QDockWidget` floating across monitors with different DPIs** can stutter on macOS. Test on user's actual hardware before committing.
5. **Single-accent rule reconciliation** — Amber brand + yellow confirmed + pink revision + green approved is technically multi-color. Justification: brand accent governs interactive chrome (buttons, focus rings, links); domain semantics govern data state. Codified in `theme/tokens.py` docstring: "Never use amber for data state. Never use yellow/pink/green for interactive chrome."
6. **qtawesome Phosphor pack** — bundled in `qtawesome>=1.3`. If a specific Phosphor glyph is missing, fall back to MaterialDesignIcons (also bundled). The `icon()` wrapper in `ui/theme/icons.py` resolves the fallback transparently so call sites don't change.
7. **Excel round-trip and inline AI suggestions are NOT in this plan.** Both are XL effort and not necessary for the redesign goal. Land as separate tickets later.

## Open Question (Defer to Implementation)

**Cockpit "Regenerate proposal" button — `.docx` export.** Phase 1 ships the cockpit *view* with the button as a stub that opens a "coming soon" modal. The actual `.docx` proposal generation lives in Phase 2 or a separate ticket — depends on whether the user has a proposal template to mirror (analogous to `ESTIMATE_FORMAT___GC.xlsx` for estimates).
