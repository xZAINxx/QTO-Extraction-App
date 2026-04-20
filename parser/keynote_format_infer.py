"""Infer per-sheet keynote ID pattern from the keynote table's ID column."""
import re
from typing import Optional


# Common keynote ID patterns (ordered by specificity)
_KNOWN_PATTERNS = [
    (re.compile(r'^[A-Z]-\d{2,3}$'), r'[A-Z]-\d{2,3}'),            # P-01, D-24
    (re.compile(r'^[A-Z]{1,3}\d{3,4}$'), r'[A-Z]{1,3}\d{3,4}'),   # E004, E024
    (re.compile(r'^\(\w+\)\s+.+'), None),                           # (D) FLOOR DRAIN — special
    (re.compile(r'^\d[A-Z]$'), r'\d[A-Z]'),                         # 1A, 2B
    (re.compile(r'^\d{1,2}$'), r'\d{1,2}'),                         # 1, 2, 3 bare digits
]


def infer_keynote_pattern(id_column_values: list[str]) -> Optional[re.Pattern]:
    """
    Given the ID column values from a keynote table, return a compiled regex
    that matches callouts on the plan, or None if no pattern inferred.
    """
    if not id_column_values:
        return None

    # Try to find the best matching known pattern
    sample = [v.strip() for v in id_column_values if v.strip()]
    if not sample:
        return None

    # Check for parenthetical format: (D) FLOOR DRAIN
    if all(re.match(r'^\([\w]+\)', s) for s in sample):
        prefix_chars = set(re.match(r'^\(([\w]+)\)', s).group(1) for s in sample if re.match(r'^\(([\w]+)\)', s))
        if prefix_chars:
            escaped = "|".join(re.escape(c) for c in sorted(prefix_chars))
            return re.compile(rf'\(({escaped})\)\s+[\w\s]+')

    # Find common prefix pattern
    first = sample[0]
    for pat_re, pat_str in _KNOWN_PATTERNS:
        if pat_re.match(first) and pat_str:
            if sum(1 for s in sample if pat_re.match(s)) >= len(sample) * 0.7:
                return pat_re

    # Build pattern from actual values using the prefix
    letter_m = re.match(r'^([A-Z]{1,3})', first)
    if letter_m and len(sample) > 1:
        prefix = letter_m.group(1)
        suffix_lengths = set()
        for s in sample:
            m = re.match(rf'^{re.escape(prefix)}[-]?(\d+)', s)
            if m:
                suffix_lengths.add(len(m.group(1)))
        if suffix_lengths:
            max_len = max(suffix_lengths)
            sep = "-" if "-" in first else ""
            return re.compile(rf'\b{re.escape(prefix)}{sep}\d{{1,{max_len}}}\b')

    # Fallback: bare digit IDs
    if all(re.match(r'^\d+$', s) for s in sample):
        return re.compile(r'\b\d{1,2}\b')

    return None


def count_callouts_on_page(page_text: str, pattern: Optional[re.Pattern]) -> dict[str, int]:
    """Count how many times each keynote ID appears in the plan text."""
    if pattern is None:
        return {}
    counts: dict[str, int] = {}
    for m in pattern.finditer(page_text):
        tag = m.group(0).strip()
        counts[tag] = counts.get(tag, 0) + 1
    return counts
