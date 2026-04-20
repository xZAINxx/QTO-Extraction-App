"""Classify notes as 'scope' (work items) vs 'reference' (codes/standards)."""
import re

_SCOPE_RE = re.compile(
    r'\b(install|provide|furnish|remove|patch|coordinate|replace|repair|demolish|'
    r'construct|apply|seal|attach|secure|clean|prime|paint|cut|core|drill|anchor|'
    r'verify|protect|brace|block|frame|flash|wrap|coat|grout|fill|cap|rout)\b',
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r'\b(aisc|astm|ansi|per code|per section|see drawing|reference|standard|'
    r'building code|nfpa|nyc|nybc|local law|typical|n\.t\.s|note:)\b',
    re.IGNORECASE,
)


def classify(text: str, ai_client=None) -> str:
    """Return 'scope' or 'reference'. Uses keyword heuristic; delegates to AI when uncertain."""
    scope_hits = len(_SCOPE_RE.findall(text))
    ref_hits = len(_REFERENCE_RE.findall(text))

    if scope_hits > ref_hits:
        return "scope"
    if ref_hits > scope_hits:
        return "reference"

    # Uncertain — delegate to AI if available
    if ai_client:
        return ai_client.classify_scope_vs_reference(text)
    return "scope"  # default conservative


def filter_scope_notes(notes: list[str], ai_client=None) -> list[str]:
    return [n for n in notes if classify(n, ai_client) == "scope"]
