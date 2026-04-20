# Zeconic QTO Extraction Tool

A PyQt6 desktop application that extracts Quantity Takeoff (QTO) line items from architectural PDF drawing sets and exports them to a formatted GC estimate Excel template.

Built for [Zeconic](https://zeconic.com) to accelerate construction cost estimating on H2M and similar multi-discipline drawing sets.

---

## Features

- **Hybrid extraction** — vector text (PyMuPDF + pdfplumber) with Claude Vision fallback for rasterized pages
- **Claude-only mode** — send every page to Claude Vision for fully scanned drawing sets
- **CSI MasterFormat grouping** — auto-classifies items into Divisions 02–32
- **GC estimate template** — exports directly into `ESTIMATE_FORMAT___GC.xlsx` with live formulas preserved
- **SQLite result cache** — re-opens instantly on repeat runs
- **Live token tracking** — real-time API cost display in the UI
- **Dark theme UI** — matches Zeconic CPM app aesthetic

---

## Setup

### Requirements

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)

### Install

```bash
git clone https://github.com/xZAINxx/QTO-Extraction-App.git
cd QTO-Extraction-App
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### API Key

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

The app loads it automatically on startup. The `.env` file is git-ignored and never committed.

---

## Usage

### GUI

```bash
source venv/bin/activate
python3 main.py
```

1. Drag and drop a PDF drawing set (or use the file picker)
2. Fill in project metadata (optional)
3. Click **Run QTO**
4. Review extracted rows — amber rows flagged for manual review
5. Click **Export .xlsx**

### Extraction Modes

Set `extraction_mode` in `config.yaml`:

| Mode | When to use |
|------|-------------|
| `hybrid` | Drawing sets with vector text (most digital PDFs) |
| `claude_only` | Fully scanned / rasterized drawing sets |

---

## Project Structure

```
qto_tool/
├── main.py                  # Entry point
├── config.yaml              # App configuration
├── ESTIMATE_FORMAT___GC.xlsx # GC estimate template
├── ai/
│   ├── client.py            # Anthropic API wrapper (caching, vision, classification)
│   ├── csi_classifier.py    # CSI MasterFormat classifier
│   └── description_normalizer.py
├── core/
│   ├── assembler.py         # Orchestrates page extraction → QTO rows
│   ├── xlsx_exporter.py     # Populates GC estimate template
│   ├── cache.py             # SQLite result cache
│   ├── qto_row.py           # QTORow dataclass
│   └── token_tracker.py     # Live token/cost tracking
├── parser/
│   ├── pdf_splitter.py      # Page classification (plan/demo/schedule/detail)
│   ├── table_detector.py    # Finds Type A/B/C/D tables on each page
│   ├── table_extractor.py   # Extracts rows from detected tables
│   ├── title_block_reader.py
│   └── ...
├── ui/
│   ├── main_window.py       # Main window + extraction worker thread
│   ├── results_table.py     # Filterable QTO results table
│   ├── upload_panel.py      # PDF drag-and-drop + metadata form
│   └── ...
└── tests/
    └── test_extractor.py    # Smoke tests against fixture PDF
```

---

## Configuration

`config.yaml` key settings:

```yaml
extraction_mode: hybrid        # hybrid | claude_only
model: claude-sonnet-4-6
max_tokens_per_page_call: 8000
confidence_review_threshold: 0.75

output_dir: ./output
cache_dir: ./cache
template_path: ./ESTIMATE_FORMAT___GC.xlsx
```

---

## Running Tests

```bash
source venv/bin/activate
python3 -m pytest tests/ -v
```

Requires the fixture PDF at `tests/fixtures/HBT_drawings.pdf`.

---

## Table Types

| Type | Source | Examples |
|------|--------|---------|
| A | Keynote / General Notes tables | Plumbing keynotes, scope notes |
| B | Symbol / Hatch legends | Material legends |
| C | Schedules | Door, finish, equipment schedules |
| D | Count / Summary tables | Fixture counts, quantity summaries |

---

## License

Private — Zeconic internal tooling.
