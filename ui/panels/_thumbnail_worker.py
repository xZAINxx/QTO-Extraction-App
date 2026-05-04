"""Background thumbnail rendering for ``SheetRail``.

Pulled out of ``sheet_rail.py`` to keep that module under the 400-line
budget. ``QRunnable`` has no native signal support, so we ship a
companion ``QObject`` (`_WorkerSignals`) that carries the result back to
the GUI thread.

The worker is failure-tolerant — corrupt pages, missing files, or a
mocked ``fitz`` under tests all hit the ``except`` branch and emit
``failed`` rather than crashing the worker thread.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QRunnable, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QPixmapCache

_LOG = logging.getLogger(__name__)

_THUMB_DPI = 50


class _WorkerSignals(QObject):
    """Signal proxy companion — ``QRunnable`` cannot emit signals itself."""

    rendered = pyqtSignal(int, QPixmap)
    failed = pyqtSignal(int, str)


class _ThumbnailWorker(QRunnable):
    """Render one PDF page to a thumbnail QPixmap on a thread-pool worker."""

    def __init__(
        self,
        pdf_path: str,
        page_num: int,
        thumb_size: QSize,
        cache_key: str,
    ) -> None:
        super().__init__()
        self.pdf_path = pdf_path
        self.page_num = page_num
        self.thumb_size = QSize(thumb_size)
        self.cache_key = cache_key
        self.signals = _WorkerSignals()

    def run(self) -> None:  # pragma: no cover — exercised via QThreadPool.
        cached = QPixmapCache.find(self.cache_key)
        if cached is not None and not cached.isNull():
            self.signals.rendered.emit(self.page_num, cached)
            return
        try:
            import fitz  # local import — keeps test mocking surface tight
            doc = fitz.open(self.pdf_path)
            page = doc[self.page_num - 1]
            mat = fitz.Matrix(_THUMB_DPI / 72, _THUMB_DPI / 72)
            raw = page.get_pixmap(matrix=mat, alpha=False)
            img = QImage(
                raw.samples, raw.width, raw.height, raw.stride,
                QImage.Format.Format_RGB888,
            ).copy()
            doc.close()
            pix = QPixmap.fromImage(img).scaled(
                self.thumb_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            QPixmapCache.insert(self.cache_key, pix)
            self.signals.rendered.emit(self.page_num, pix)
        except Exception as exc:  # corrupt file, MagicMock fitz, etc.
            _LOG.debug("thumbnail render failed page %d: %s", self.page_num, exc)
            self.signals.failed.emit(self.page_num, str(exc))


__all__ = ["_ThumbnailWorker", "_WorkerSignals"]
