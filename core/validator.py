"""Validate QTO rows and flag issues."""
from core.qto_row import QTORow


def validate(rows: list[QTORow], threshold: float = 0.75) -> list[str]:
    """Return list of warning messages. Also sets needs_review on low-confidence rows."""
    warnings = []
    for i, row in enumerate(rows):
        if row.is_header_row:
            continue
        if not row.description:
            warnings.append(f"Row {i}: empty description")
        if row.qty <= 0:
            warnings.append(f"Row {i} ({row.description[:30]}): qty is {row.qty}")
        if not row.units:
            warnings.append(f"Row {i} ({row.description[:30]}): missing units")
        if row.confidence < threshold:
            row.needs_review = True
        if row.extraction_method == "failed":
            warnings.append(f"Row {i}: extraction failed for page {row.source_page}")
    return warnings
