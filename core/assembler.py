"""Assemble parser output + AI classification into final QTORow list."""
import re
from typing import Callable, Optional

import fitz

from core.qto_row import QTORow
from parser.pdf_splitter import PageInfo
from parser.title_block_reader import TitleBlockInfo, read_title_block
from parser.table_detector import detect_tables
from parser.table_extractor import (
    extract_type_a, extract_type_b, extract_type_c, extract_type_d,
)
from parser.scale_detector import detect_scale
from parser.geometry_reader import read_geometry
from parser.scope_note_classifier import filter_scope_notes
from parser.ocr_fallback import extract_keynote_table_vision
from ai.description_normalizer import DescriptionComposer
from ai.csi_classifier import CSIClassifier


def _sheet_sort_key(sheet: str) -> tuple:
    """Sort sheet numbers like A-061 < A-100 < A-101 correctly."""
    m = re.match(r'^([A-Za-z]+)-?(\d+)', sheet.strip())
    if m:
        return (m.group(1).upper(), int(m.group(2)))
    return (sheet.upper(), 0)


class Assembler:
    def __init__(self, config: dict, ai_client, tracker):
        self._config = config
        self._ai = ai_client
        self._composer = DescriptionComposer(ai_client)
        self._classifier = CSIClassifier(ai_client, config.get("csi_keywords", {}))
        self._units_canonical: dict = config.get("units_canonical", {})
        self._pdf_path = ""
        self._raw_items: list[dict] = []

    def process_page(
        self,
        page: fitz.Page,
        page_info: PageInfo,
        pdf_path: str,
    ) -> list[QTORow]:
        """Process one page and return its QTO rows."""
        self._pdf_path = pdf_path
        rows: list[QTORow] = []

        if page_info.skip:
            return rows

        if self._config.get("extraction_mode") == "claude_only":
            try:
                title_info = read_title_block(page, self._config, self._ai)
                mat = fitz.Matrix(2, 2)
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes("png")
                items = self._ai.extract_page_claude_only(img_bytes)
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

        try:
            title_info = read_title_block(page, self._config, self._ai)
            tables = detect_tables(page, pdf_path, page_info.page_num)
            scale = detect_scale(page)

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

            # Geometry quantities (supplementary — don't double-count keynotes)
            if page_info.page_type in ("PLAN_DEMO", "PLAN_CONSTRUCTION", "ELEVATION") and scale:
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
        sheet_number = title.sheet_number or ""
        keynote_ref = f"{keynote_id}/{sheet_number}" if keynote_id else sheet_number
        category_label = item.get("category_label", "")

        desc = self._composer.compose(desc_raw, sheet_number, keynote_ref)
        division, conf = self._classifier.classify(desc)

        drawings = sheet_number
        details = f"{category_label} {keynote_ref}".strip() if category_label else keynote_ref

        raw_units = item.get("units", "EA") or "EA"
        units = self._normalize_units(raw_units)

        threshold = self._config.get("confidence_review_threshold", 0.75)

        return QTORow(
            drawings=drawings,
            details=details,
            description=desc,
            qty=float(item.get("qty", 1) or 1),
            units=units,
            trade_division=division,
            source_page=page_info.page_num,
            source_sheet=sheet_number,
            extraction_method=method,
            confidence=conf,
            needs_review=(conf < threshold) or (method == "vision"),
        )

    def _normalize_units(self, units: str) -> str:
        return self._units_canonical.get(units, units)

    def group_by_section(self, rows: list[QTORow]) -> list[QTORow]:
        """
        Return only data rows (no header rows) sorted by sheet number then details.
        Assigns s_no and tag counters sequentially.
        """
        data_rows = [r for r in rows if not r.is_header_row]
        data_rows.sort(key=lambda r: (_sheet_sort_key(r.drawings), r.details))

        for i, row in enumerate(data_rows, 1):
            row.s_no = i
            row.tag = str(i)

        return data_rows
