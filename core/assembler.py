"""Assemble parser output + AI composition into the final QTORow list.

Phase 1 refactor:
- Routes per-zone (legend / schedule / plan-body / notes) instead of
  per-page; saves vision tokens by cropping tightly.
- Drops CSI division grouping; the GC estimate template no longer uses it.
- ``sort_by_sheet`` orders rows by (sheet_number, details, original_index)
  so identical sheets stay grouped and pre-existing keynote ordering is
  preserved within a sheet.
"""
from __future__ import annotations

import re
from typing import Optional

import fitz

from core.qto_row import QTORow
from parser.pdf_splitter import PageInfo
from parser.title_block_reader import TitleBlockInfo, read_title_block
from parser.table_detector import detect_tables
from parser.table_extractor import (
    extract_type_a, extract_type_b, extract_type_d,
)
from parser.scale_detector import detect_scale
from parser.geometry_reader import read_geometry
from parser.ocr_fallback import extract_keynote_table_vision
from parser.legend_extractor import extract_legend_items
from parser.schedule_extractor import extract_schedules
from parser.allowance_extractor import extract_allowances
from parser.zone_segmenter import segment as segment_zones
from parser.symbol_detector import detect_symbols_in_zone, to_qto_items
from ai.description_composer import DescriptionComposer


def _sheet_sort_key(sheet: str) -> tuple:
    """Sort sheet numbers like A-061 < A-100 < A-101 correctly."""
    m = re.match(r'^([A-Za-z]+)-?(\d+)', (sheet or "").strip())
    if m:
        return (m.group(1).upper(), int(m.group(2)))
    return ((sheet or "").upper(), 0)


class Assembler:
    def __init__(self, config: dict, ai_client, tracker):
        self._config = config
        self._ai = ai_client
        self._composer = DescriptionComposer(ai_client)
        self._units_canonical: dict = config.get("units_canonical", {})
        self._pdf_path = ""
        self._raw_items: list[dict] = []
        # Phase 7 — remember per-row compose context so we can re-resolve
        # after a batch flush. Keyed by ``id(row)`` so we don't depend on
        # row equality semantics.
        self._compose_ctx: dict[int, tuple[str, str, str]] = {}

    # ── Entry point ────────────────────────────────────────────────────────

    def process_page(
        self,
        page: fitz.Page,
        page_info: PageInfo,
        pdf_path: str,
    ) -> list[QTORow]:
        self._pdf_path = pdf_path
        if page_info.skip:
            return []

        if self._config.get("extraction_mode") == "claude_only":
            return self._process_claude_only(page, page_info)

        rows: list[QTORow] = []
        try:
            title_info = read_title_block(page, self._config, self._ai)

            # T-002 cover sheet is fully delegated to allowance extractor.
            if "T-002" in (title_info.sheet_number or "").upper():
                items = extract_allowances(page, title_info, self._ai)
                for item in items:
                    item.setdefault("drawings", "T-002")
                    row = self._make_row(item, title_info, page_info, "allowance")
                    if row:
                        rows.append(row)
                return rows

            # Segment the page once; reuse for legend + schedule.
            try:
                zones = segment_zones(page, page_num=page_info.page_num)
            except Exception:
                zones = None

            # Legend zones → composed scope rows.
            legend_items = extract_legend_items(page, self._ai, zones=zones)
            for item in legend_items:
                detail_refs = item.get("detail_refs") or []
                sheet = title_info.sheet_number or ""
                legend_ref = f"LEGEND/{sheet.replace('-', '')}" if sheet else "LEGEND"
                details = f"{legend_ref} & {detail_refs[0]}" if detail_refs else legend_ref
                synthetic = {
                    "description": item.get("work_description", ""),
                    "units": item.get("units", "EA"),
                    "qty": item.get("qty") or 1,
                    "details_override": details,
                }
                row = self._make_row(synthetic, title_info, page_info, "legend")
                if row:
                    rows.append(row)

            # Schedule zones → schedule extractor (pdfplumber-first, vision fallback).
            if zones is not None and zones.schedules:
                schedule_items = extract_schedules(page, zones, pdf_path, self._ai)
                for item in schedule_items:
                    row = self._make_row(item, title_info, page_info, "schedule")
                    if row:
                        rows.append(row)

            # Plan-body zones → local CV symbol counts (Phase 2). Silently
            # no-ops if ultralytics or YOLO weights aren't available.
            if (
                zones is not None
                and zones.plan_bodies
                and self._cv_enabled(page_info)
            ):
                try:
                    counts = detect_symbols_in_zone(page, zones)
                except Exception:
                    counts = []
                if counts:
                    sheet = title_info.sheet_number or ""
                    for item in to_qto_items(counts, sheet):
                        row = self._make_row(item, title_info, page_info, "cv_count")
                        if row:
                            rows.append(row)

            # Legacy table detector for keynotes / count tables that the
            # zone segmenter doesn't yet recognise.
            tables = detect_tables(page, pdf_path, page_info.page_num)
            scale = detect_scale(page)
            schedule_zone_count = len(zones.schedules) if zones else 0

            for region in tables:
                try:
                    if region.table_type == "A":
                        items = extract_type_a(region, page, title_info, self._ai)
                        if not items:
                            items = extract_keynote_table_vision(page, self._ai)
                        for item in items:
                            row = self._make_row(item, title_info, page_info, "vector")
                            if row:
                                rows.append(row)
                    elif region.table_type == "B":
                        items = extract_type_b(region, page, title_info, self._ai)
                        for item in items:
                            row = self._make_row(item, title_info, page_info, "vision")
                            if row:
                                rows.append(row)
                    elif region.table_type == "C":
                        # If the zone segmenter already handled schedules, skip
                        # the legacy Type-C path to avoid double extraction.
                        if schedule_zone_count > 0:
                            continue
                        from parser.table_extractor import extract_type_c
                        items = extract_type_c(region, title_info, self._ai)
                        for item in items:
                            row = self._make_row(item, title_info, page_info, "schedule")
                            if row:
                                rows.append(row)
                    elif region.table_type == "D":
                        items = extract_type_d(region, title_info)
                        for item in items:
                            row = self._make_row(item, title_info, page_info, "summary_table")
                            if row:
                                rows.append(row)
                except Exception as e:
                    rows.append(QTORow(
                        drawings=title_info.sheet_number,
                        description=f"TABLE_EXTRACTION_FAILED [{region.table_type}] — {type(e).__name__}",
                        source_page=page_info.page_num,
                        extraction_method="failed",
                        confidence=0.0,
                        needs_review=True,
                    ))

            # Geometry-derived supplementary rows (still flagged for review).
            if (
                page_info.page_type in ("PLAN_DEMO", "PLAN_CONSTRUCTION", "ELEVATION")
                and scale
            ):
                geo = read_geometry(page, scale)
                if geo.get("areas_sf", 0) > 0:
                    rows.append(QTORow(
                        drawings=title_info.sheet_number,
                        description="Floor area (measured from geometry)",
                        qty=geo["areas_sf"],
                        units="SQ FT",
                        source_page=page_info.page_num,
                        source_sheet=title_info.sheet_number,
                        extraction_method="vector",
                        confidence=0.8,
                        needs_review=True,
                    ))
        except Exception:
            rows.append(QTORow(
                drawings="",
                description=f"EXTRACTION_FAILED — page {page_info.page_num}",
                source_page=page_info.page_num,
                extraction_method="failed",
                confidence=0.0,
                needs_review=True,
            ))

        return rows

    # ── Helpers ────────────────────────────────────────────────────────────

    def _cv_enabled(self, page_info: PageInfo) -> bool:
        """CV counting only fires on actual plan/elevation pages and when
        the user has opted in via ``config.cv.enabled`` (default ``True``)."""
        cv_cfg = self._config.get("cv", {}) or {}
        if cv_cfg.get("enabled") is False:
            return False
        return page_info.page_type in (
            "PLAN_DEMO", "PLAN_CONSTRUCTION", "ELEVATION", "REFLECTED_CEILING",
        )

    def _process_claude_only(
        self, page: fitz.Page, page_info: PageInfo,
    ) -> list[QTORow]:
        rows: list[QTORow] = []
        try:
            title_info = read_title_block(page, self._config, self._ai)
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            items = self._ai.extract_full_page_vision(img_bytes)
            for item in items:
                row = self._make_row(item, title_info, page_info, "claude_only")
                if row:
                    rows.append(row)
        except Exception:
            rows.append(QTORow(
                description=f"EXTRACTION_FAILED — page {page_info.page_num}",
                source_page=page_info.page_num,
                extraction_method="failed",
                confidence=0.0,
                needs_review=True,
            ))
        return rows

    def _make_row(
        self,
        item: dict,
        title: TitleBlockInfo,
        page_info: PageInfo,
        method: str,
    ) -> Optional[QTORow]:
        desc_raw = item.get("description", "")
        if not desc_raw:
            return None

        keynote_id = item.get("id", "")
        sheet_number = item.get("drawings") or title.sheet_number or ""
        keynote_ref = f"{keynote_id}/{sheet_number}" if keynote_id else sheet_number
        category_label = item.get("category_label", "")

        desc = self._composer.compose(desc_raw, sheet_number, keynote_ref)
        compose_ctx = (desc_raw, sheet_number, keynote_ref)

        drawings = sheet_number
        if item.get("details_override"):
            details = item["details_override"]
        else:
            details = (
                f"{category_label} {keynote_ref}".strip()
                if category_label else keynote_ref
            )

        raw_units = item.get("units", "EA") or "EA"
        units = self._normalize_units(raw_units)

        threshold = self._config.get("confidence_review_threshold", 0.75)
        # Confidence: high for vector extractors, lower for vision-derived.
        conf = 0.95 if method in ("vector", "allowance", "schedule") else 0.7
        if method == "claude_only":
            conf = 0.6
        elif method == "cv_count":
            # Symbol-count rows always need a human glance until calibrated.
            conf = 0.7

        row = QTORow(
            drawings=drawings,
            details=details,
            description=desc,
            qty=float(item.get("qty", 1) or 1),
            units=units,
            source_page=page_info.page_num,
            source_sheet=sheet_number,
            extraction_method=method,
            confidence=conf,
            needs_review=(conf < threshold) or (method in ("vision", "claude_only")),
        )
        if getattr(self._ai, "cost_saver_mode", False):
            self._compose_ctx[id(row)] = compose_ctx
        return row

    def _normalize_units(self, units: str) -> str:
        return self._units_canonical.get(units, units)

    def sort_by_sheet(self, rows: list[QTORow]) -> list[QTORow]:
        """Stable sort by (sheet, details, original_order). Renumbers s_no."""
        data_rows = [r for r in rows if not r.is_header_row]
        # Preserve original order as tiebreaker for stable, deterministic output.
        indexed = list(enumerate(data_rows))
        indexed.sort(key=lambda pair: (
            _sheet_sort_key(pair[1].drawings),
            pair[1].details or "",
            pair[0],
        ))
        sorted_rows = [r for _, r in indexed]
        for i, row in enumerate(sorted_rows, 1):
            row.s_no = i
            row.tag = str(i)
        return sorted_rows

    def flush_batched_compose(self, rows: list[QTORow], *, on_progress=None) -> int:
        """Phase 7 — resolve queued compose calls in one batched API run, and
        Phase 8 — orchestrator review of low-confidence rows.

        The batch-flush body only runs when ``cost_saver_mode == True`` and
        there are queued compose calls. The orchestrator review runs
        unconditionally whenever the AI client exposes
        ``review_low_confidence_rows`` (multi-agent path), regardless of
        cost-saver state.

        Returns the number of rows whose description was upgraded from the
        raw upper-case fallback to a properly composed description.
        """
        upgraded = 0
        if getattr(self._ai, "cost_saver_mode", False) and getattr(self._ai, "pending_compose_count", 0):
            self._ai.flush_pending_compose(on_progress=on_progress)
            for row in rows:
                if row.is_header_row:
                    continue
                ctx = self._compose_ctx.get(id(row))
                if ctx is None:
                    continue
                raw, sheet, keynote_ref = ctx
                new_desc = self._composer.compose(raw, sheet, keynote_ref)
                if new_desc and new_desc != row.description:
                    row.description = new_desc
                    upgraded += 1
            self._compose_ctx.clear()

        review = getattr(self._ai, "review_low_confidence_rows", None)
        if review is not None:
            threshold = self._config.get("confidence_review_threshold", 0.75)
            try:
                review(rows, threshold)
            except Exception:
                # Review failures must never break the assembler return.
                pass
        return upgraded
