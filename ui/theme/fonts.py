"""Font loader for the bundled Geist Sans + Geist Mono families.

Why this is its own module:
    * The font files live under ``assets/fonts/Geist/`` (variable .ttf
      flavor). They are NOT version-controlled to keep the repository
      lightweight; users follow the README in that folder to drop them in.
    * Loading goes through ``QFontDatabase.addApplicationFont`` which makes
      the family available to every QSS rule that names ``"Geist"`` or
      ``"Geist Mono"``.
    * If the files are missing, the loader logs a warning and the UI keeps
      working with whatever sans/mono families the platform exposes — no
      hard failure, no startup crash.

Important call ordering:
    ``QApplication`` MUST exist before ``load_fonts()`` runs. Without an
    application instance, ``QFontDatabase`` silently no-ops on every
    ``addApplicationFont`` call. The wrapper ``apply_theme()`` in
    ``ui.theme`` enforces this by accepting the QApplication as its first
    argument and calling ``load_fonts()`` after it.

The loader is idempotent: calling it twice does not re-register fonts,
because ``QFontDatabase.addApplicationFont`` deduplicates by file path
internally.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)


class FontLoadResult(TypedDict):
    sans_loaded: bool
    mono_loaded: bool
    families: list[str]
    errors: list[str]


# Resolve the asset directory relative to the project root (one level up
# from ``ui/``). Using ``Path(__file__).resolve()`` keeps this stable
# regardless of the current working directory.
_DEFAULT_FONT_DIR = (
    Path(__file__).resolve().parent.parent.parent / "assets" / "fonts" / "Geist"
)


def load_fonts(font_dir: Path | None = None) -> FontLoadResult:
    """Discover and register every ``.ttf`` in the Geist asset folder.

    Returns a dict reporting which families became available, the union of
    all family names registered, and any errors encountered. Safe to call
    multiple times.
    """
    result: FontLoadResult = {
        "sans_loaded": False,
        "mono_loaded": False,
        "families": [],
        "errors": [],
    }

    target_dir = font_dir or _DEFAULT_FONT_DIR
    if not target_dir.exists():
        msg = f"Geist font directory missing at {target_dir}; falling back to system default."
        logger.warning(msg)
        result["errors"].append(msg)
        return result

    try:
        from PyQt6.QtGui import QFontDatabase
    except ImportError as exc:  # pragma: no cover — PyQt6 is a hard dep.
        msg = f"PyQt6 not importable: {exc}"
        logger.error(msg)
        result["errors"].append(msg)
        return result

    ttf_files = sorted(target_dir.glob("*.ttf"))
    if not ttf_files:
        msg = (
            f"No .ttf files found in {target_dir}; the UI will use the "
            f"platform default sans/mono. See {target_dir / 'README.md'}."
        )
        logger.warning(msg)
        result["errors"].append(msg)
        return result

    seen_families: set[str] = set()
    for ttf in ttf_files:
        font_id = QFontDatabase.addApplicationFont(str(ttf))
        if font_id == -1:
            msg = f"Failed to register font: {ttf.name}"
            logger.warning(msg)
            result["errors"].append(msg)
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            seen_families.add(family)
            if "Mono" in family:
                result["mono_loaded"] = True
            else:
                result["sans_loaded"] = True

    result["families"] = sorted(seen_families)
    if result["sans_loaded"] or result["mono_loaded"]:
        logger.info(
            "Loaded Geist fonts: %s",
            ", ".join(result["families"]) or "(none)",
        )
    return result


__all__ = ["FontLoadResult", "load_fonts"]
