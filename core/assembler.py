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
from ai.description_normalizer import DescriptionNormalizer
from ai.csi_classifier import CSIClassifier


CSI_ORDER = [
    "DIVISION 02", "DIVISION 03", "DIVISION 04", "DIVISION 05",
    "DIVISION 06", "DIVISION 07", "DIVISION 08", "DIVISION 09",
    "DIVISION 21", "DIVISION 22", "DIVISION 23", "DIVISION 26", "DIVISION 32",
]

CSI_LABELS = {
    "DIVISION 02": "DIVISION 02 — DEMOLITION",
    "DIVISION 03": "DIVISION 03 — CONCRETE",
    "DIVISION 04": "DIVISION 04 — MASONRY",
    "DIVISION 05": "DIVISION 05 — METALS",
    "DIVISION 06": "DIVISION 06 — WOOD & PLASTICS",
    "DIVISION 07": "DIVISION 07 — THERMAL & MOISTURE PROTECTION",
    "DIVISION 08": "DIVISION 08 — DOORS & WINDOWS",
    "DIVISION 09": "DIVISION 09 — FINISHES",
    "DIVISION 21": "DIVISION 21 — FIRE SUPPRESSION",
    "DIVISION 22": "DIVISION 22 — PLUMBING",
    "DIVISION 23": "DIVISION 23 — MECHANICAL (HVAC)",
    "DIVISION 26": "DIVISION 26 — ELECTRICAL",
    "DIVISION 32": "DIVISION 32 — EXTERIOR IMPROVEMENTS",
}


class Assembler:
    def __init__(self, config: dict, ai_client, tracker):
        self._config = config
        self._ai = ai_client
        self._normalizer = DescriptionNormalizer(ai_client)
        self._classifier = CSIClassifier(ai_client, config.get("csi_keywords", {}))
        self._pdf_path = ""
        self._raw_items: list[dict] = []

    def process_page(
        self,
        page: fitz.Page,
        page_info: PageInfo,
        pdf_path: str,
    ) -> list[QTORow]:
        """Process one page and return its QTO rows (unordered by CSI)."""
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
                        drawings_details=title_info.sheet_number,
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
                        drawings_details=title_info.sheet_number,
                        description=f"Floor area (measured from geometry)",
                        qty=geo["areas_sf"],
                        units="SF",
                        source_page=page_info.page_num,
                        source_sheet=title_info.sheet_number,
                        extraction_method="vector",
                        confidence=0.8,
                        needs_review=True,
                    ))

        except Exception:
            rows.append(QTORow(
                drawings_details="",
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

        desc = self._normalizer.normalize(desc_raw)
        division, conf = self._classifier.classify(desc)
        keynote_id = item.get("id", "")
        drawings_details = f"{title.sheet_number} / {keynote_id}" if keynote_id else title.sheet_number

        qty = float(item.get("qty", 1) or 1)
        units = item.get("units", "EA")

        threshold = self._config.get("confidence_review_threshold", 0.75)

        return QTORow(
            drawings_details=drawings_details,
            description=desc,
            qty=qty,
            units=units,
            trade_division=division,
            source_page=page_info.page_num,
            source_sheet=title.sheet_number,
            extraction_method=method,
            confidence=conf,
            needs_review=(conf < threshold) or (method == "vision"),
        )

    def group_by_csi(self, rows: list[QTORow]) -> list[QTORow]:
        """
        Sort rows by CSI division order, insert section header rows,
        assign s_no and tag counters.
        """
        by_division: dict[str, list[QTORow]] = {}
        for r in rows:
            div = r.trade_division or "DIVISION 09"
            by_division.setdefault(div, []).append(r)

        result: list[QTORow] = []
        section_counter = 1

        for div_key in CSI_ORDER:
            div_rows = by_division.get(div_key, [])
            if not div_rows:
                continue

            label = CSI_LABELS.get(div_key, div_key)
            result.append(QTORow(
                s_no=section_counter,
                description=label,
                is_header_row=True,
                trade_division=div_key,
            ))

            tag_counter = 1
            for row in div_rows:
                row.s_no = section_counter
                row.tag = str(tag_counter)
                result.append(row)
                tag_counter += 1

            section_counter += 1

        # Unclassified rows
        unclassified = [r for k, v in by_division.items() for r in v if k not in CSI_ORDER]
        if unclassified:
            result.append(QTORow(
                s_no=section_counter,
                description="OTHER",
                is_header_row=True,
            ))
            for i, row in enumerate(unclassified, 1):
                row.s_no = section_counter
                row.tag = str(i)
                result.append(row)

        return result
