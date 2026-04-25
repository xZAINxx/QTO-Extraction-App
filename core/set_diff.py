"""Set comparison — detect what changed between two PDF revisions.

Phase-5 deliverable. The pipeline is intentionally local-first:

    1. Pair pages between *old* and *new* by sheet ID (uses
       :func:`parser.title_block_reader.read_sheet_number`).
    2. For each pair, render both pages to grayscale uint8 at 150 DPI.
    3. Compute AKAZE feature descriptors and brute-force match. Estimate
       a homography with ``cv2.findHomography`` (RANSAC). If we don't
       get >= ``_MIN_MATCHES`` reliable matches, we mark the page as
       ``status='structural'`` and fall back to flagging the entire
       sheet as changed.
    4. Warp the new page onto the old page's coordinate frame. Compute
       an absolute-difference image (with a 12-pixel tolerance for
       anti-aliasing wiggle), threshold and morphologically close.
    5. Connected-component analysis groups diff pixels into bounding
       boxes. Tiny boxes (<24 px on either side) are dropped.
    6. For each surviving cluster, ask Sonnet via
       :meth:`ai.client.AIClient.describe_diff_cluster` to summarise the
       change in one sentence — passing both crops, prompt-cached.
    7. Cluster bboxes are reprojected back to *old* PDF mediabox
       coordinates so the UI overlay can highlight the right place.

The result is a :class:`SetDiffResult` listing every change pair with
per-cluster descriptions. Callers can use
:func:`changed_page_numbers` to drive partial re-extraction (skip
unchanged pages, pull cached rows for them, only re-run the extractor
on the deltas).

All heavy CV work uses lazy ``cv2`` imports so cold start stays fast.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

import fitz
import numpy as np

from parser.title_block_reader import read_title_block


_LOG = logging.getLogger(__name__)
_RENDER_DPI = 100             # 100 DPI is plenty for diff detection; keeps page ≈2MP
_PIXEL_TOLERANCE = 12         # absdiff tolerance to ignore anti-alias jitter
_MIN_MATCHES = 12             # minimum feature matches to trust homography
_MIN_CLUSTER_PX = 18          # drop diff clusters smaller than 18px on a side
_MAX_CLUSTERS_PER_PAGE = 25   # cap descriptions/page to keep AI cost bounded
_HASH_DPI = 36                # small preview — must be high enough to surface
                              # localized rectangle/symbol changes
_HASH_MEAN_THRESHOLD = 0.5    # mean abs-diff (~0..255) below which whole-page
                              # changes get a free pass
_HASH_MAX_THRESHOLD = 30      # max-pixel-delta floor — guarantees that even a
                              # single rectangle change defeats the pre-filter
_DEFAULT_TB_CONFIG: dict = {"title_block_region": {"pct": 0.18}}


PageStatus = Literal["unchanged", "modified", "added", "removed", "structural"]


@dataclass
class DiffCluster:
    """One detected change region (rect in *old* PDF coords)."""
    pdf_rect: fitz.Rect
    pixel_count: int
    description: str = ""


@dataclass
class PageDiff:
    """Diff between matching pages, or 'added' / 'removed' marker."""
    sheet_id: str
    status: PageStatus
    old_page: Optional[int] = None     # 1-indexed
    new_page: Optional[int] = None     # 1-indexed
    clusters: list[DiffCluster] = field(default_factory=list)


@dataclass
class SetDiffResult:
    old_pdf: str
    new_pdf: str
    pairs: list[PageDiff] = field(default_factory=list)

    def changed_pages(self) -> list[PageDiff]:
        return [p for p in self.pairs if p.status != "unchanged"]

    def changed_sheet_ids(self) -> list[str]:
        return [p.sheet_id for p in self.changed_pages()]

    def report_summary(self) -> str:
        added = sum(1 for p in self.pairs if p.status == "added")
        removed = sum(1 for p in self.pairs if p.status == "removed")
        modified = sum(1 for p in self.pairs if p.status == "modified")
        unchanged = sum(1 for p in self.pairs if p.status == "unchanged")
        structural = sum(1 for p in self.pairs if p.status == "structural")
        total_clusters = sum(len(p.clusters) for p in self.pairs)
        return (
            f"{modified} modified, {added} added, {removed} removed, "
            f"{structural} structural-change, {unchanged} unchanged "
            f"({total_clusters} change clusters)"
        )


def changed_page_numbers(result: SetDiffResult) -> set[int]:
    """Return the *new* page numbers that need re-extraction.

    Uses ``new_page`` for modified pages and ``new_page`` for added
    pages. ``removed`` pages obviously have no new_page so they're
    skipped (caller drops them from the cached row set instead).
    """
    out: set[int] = set()
    for p in result.pairs:
        if p.status in ("modified", "added", "structural") and p.new_page:
            out.add(p.new_page)
    return out


# ── Public entry point ───────────────────────────────────────────────────

def diff_sets(
    old_pdf: str | Path,
    new_pdf: str | Path,
    *,
    ai_client=None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    describe: bool = True,
) -> SetDiffResult:
    """Diff two PDFs end-to-end.

    Parameters
    ----------
    ai_client
        Optional :class:`ai.client.AIClient` used to describe each diff
        cluster in plain English. Pass ``None`` for a structure-only
        diff with no API calls.
    progress
        Optional callback ``(current, total, message)`` for UI updates.
    describe
        Disable to skip AI calls even if ``ai_client`` is provided.
    """
    old_path = str(old_pdf)
    new_path = str(new_pdf)

    old_doc = fitz.open(old_path)
    new_doc = fitz.open(new_path)
    try:
        old_index = _index_by_sheet(old_doc)
        new_index = _index_by_sheet(new_doc)
        all_sheets = sorted(set(old_index) | set(new_index))
        result = SetDiffResult(old_pdf=old_path, new_pdf=new_path)

        for i, sheet_id in enumerate(all_sheets, start=1):
            old_p = old_index.get(sheet_id)
            new_p = new_index.get(sheet_id)
            if progress:
                progress(i, len(all_sheets), f"Diffing {sheet_id}")
            if old_p is None and new_p is not None:
                result.pairs.append(PageDiff(
                    sheet_id=sheet_id, status="added", new_page=new_p,
                ))
                continue
            if new_p is None and old_p is not None:
                result.pairs.append(PageDiff(
                    sheet_id=sheet_id, status="removed", old_page=old_p,
                ))
                continue
            if old_p is None or new_p is None:
                continue
            pair = _diff_page_pair(
                old_doc[old_p - 1], new_doc[new_p - 1],
                sheet_id=sheet_id,
            )
            pair.old_page = old_p
            pair.new_page = new_p
            if describe and ai_client is not None and pair.clusters:
                _describe_clusters(
                    pair, old_doc[old_p - 1], new_doc[new_p - 1], ai_client,
                )
            result.pairs.append(pair)
        return result
    finally:
        old_doc.close()
        new_doc.close()


# ── Indexing & per-page diff ─────────────────────────────────────────────

def _index_by_sheet(doc: fitz.Document) -> dict[str, int]:
    """Map sheet ID → 1-indexed page number, falling back to 'page-N'."""
    out: dict[str, int] = {}
    for i in range(doc.page_count):
        page = doc[i]
        info = read_title_block(page, _DEFAULT_TB_CONFIG)
        sid = (info.sheet_number or "").strip() or f"page-{i+1}"
        if sid in out:
            sid = f"{sid}#{i+1}"
        out[sid] = i + 1
    return out


def _diff_page_pair(
    old_page: fitz.Page, new_page: fitz.Page, *, sheet_id: str,
) -> PageDiff:
    try:
        import cv2  # noqa: F401 — required for ORB/AKAZE + warpPerspective
    except Exception as exc:
        _LOG.warning("opencv missing — set_diff degrades to structural-only: %s", exc)
        return PageDiff(sheet_id=sheet_id, status="structural")

    # Cheap pre-filter: render at 25 DPI and compare mean abs diff.
    # If it's tiny, the pages are visually identical — skip the heavy work.
    if _hash_unchanged(old_page, new_page):
        return PageDiff(sheet_id=sheet_id, status="unchanged")

    old_img, _ = _render_gray(old_page)
    new_img, _ = _render_gray(new_page)
    if old_img.size == 0 or new_img.size == 0:
        return PageDiff(sheet_id=sheet_id, status="structural")

    H = _estimate_homography(old_img, new_img)
    if H is None:
        return PageDiff(sheet_id=sheet_id, status="structural")

    warped_new = _warp(new_img, H, dsize=(old_img.shape[1], old_img.shape[0]))
    diff_mask = _diff_mask(old_img, warped_new)
    clusters = _cluster_mask(diff_mask)

    if not clusters:
        return PageDiff(sheet_id=sheet_id, status="unchanged")

    # ``page.rect`` is the *display* rectangle (rotation already applied),
    # which matches the pixmap orientation we ran the diff on. Using
    # ``mediabox`` here breaks for rotated sheets.
    page_rect = old_page.rect
    ix = page_rect.width / old_img.shape[1]
    iy = page_rect.height / old_img.shape[0]
    diff_clusters: list[DiffCluster] = []
    for x, y, w, h, area in clusters[:_MAX_CLUSTERS_PER_PAGE]:
        rect = fitz.Rect(
            page_rect.x0 + x * ix,
            page_rect.y0 + y * iy,
            page_rect.x0 + (x + w) * ix,
            page_rect.y0 + (y + h) * iy,
        )
        diff_clusters.append(DiffCluster(pdf_rect=rect, pixel_count=int(area)))

    return PageDiff(
        sheet_id=sheet_id,
        status="modified" if diff_clusters else "unchanged",
        clusters=diff_clusters,
    )


def _describe_clusters(
    pair: PageDiff,
    old_page: fitz.Page,
    new_page: fitz.Page,
    ai_client,
) -> None:
    """Annotate each cluster with a one-sentence Sonnet description."""
    describe = getattr(ai_client, "describe_diff_cluster", None)
    if describe is None:
        _LOG.debug("ai_client has no describe_diff_cluster() — skipping descriptions")
        return
    for cluster in pair.clusters:
        try:
            old_png = _crop_png(old_page, cluster.pdf_rect)
            new_png = _crop_png(new_page, cluster.pdf_rect)
        except Exception as exc:
            _LOG.warning("crop failed for %s: %s", pair.sheet_id, exc)
            continue
        try:
            cluster.description = (
                describe(old_png, new_png, sheet_id=pair.sheet_id) or ""
            ).strip()
        except Exception as exc:
            _LOG.warning("describe_diff_cluster failed (%s): %s", pair.sheet_id, exc)
            cluster.description = ""


# ── Image helpers (lazy cv2 imports) ────────────────────────────────────

def _render_gray(page: fitz.Page) -> tuple[np.ndarray, fitz.Matrix]:
    zoom = _RENDER_DPI / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        (pix.height, pix.width, pix.n)
    )
    if pix.n >= 3:
        # Manual BGR→GRAY using ITU BT.601 weights (no cv2 dependency here).
        gray = (0.114 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.299 * arr[:, :, 2])
        gray = gray.astype(np.uint8)
    else:
        gray = arr[:, :, 0]
    return gray, mat


def _estimate_homography(old_img: np.ndarray, new_img: np.ndarray) -> Optional[np.ndarray]:
    """ORB feature match + RANSAC homography. Falls back to AKAZE on failure.

    ORB is ~5–10x faster than AKAZE on architectural sheets while staying
    accurate enough for axis-aligned diffs. We only fall back to AKAZE
    when ORB can't find enough matches (e.g. low-texture pages).
    """
    import cv2
    H = _try_features(old_img, new_img, cv2.ORB_create(nfeatures=4000), cv2.NORM_HAMMING)
    if H is not None:
        return H
    return _try_features(old_img, new_img, cv2.AKAZE_create(), cv2.NORM_HAMMING)


def _try_features(old_img, new_img, detector, norm) -> Optional[np.ndarray]:
    import cv2
    kp1, des1 = detector.detectAndCompute(old_img, None)
    kp2, des2 = detector.detectAndCompute(new_img, None)
    if des1 is None or des2 is None or len(kp1) < _MIN_MATCHES or len(kp2) < _MIN_MATCHES:
        return None
    matcher = cv2.BFMatcher(norm, crossCheck=False)
    raw = matcher.knnMatch(des1, des2, k=2)
    good = []
    for pair in raw:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < 0.75 * n.distance:
            good.append(m)
    if len(good) < _MIN_MATCHES:
        return None
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    # We warp NEW onto OLD, so map new (dst) -> old (src).
    H, mask = cv2.findHomography(dst, src, cv2.RANSAC, 4.0)
    if H is None:
        return None
    inliers = int(mask.sum()) if mask is not None else 0
    if inliers < _MIN_MATCHES:
        return None
    return H


def _hash_unchanged(old_page: fitz.Page, new_page: fitz.Page) -> bool:
    """Cheap thumbnail-level diff to short-circuit identical pages.

    Uses both the mean and max absolute differences so a single small
    rectangle change still defeats the pre-filter (mean stays low but
    max spikes to ~255 in the changed region).
    """
    try:
        zoom = _HASH_DPI / 72.0
        mat = fitz.Matrix(zoom, zoom)
        a = old_page.get_pixmap(matrix=mat, alpha=False)
        b = new_page.get_pixmap(matrix=mat, alpha=False)
        if a.width != b.width or a.height != b.height:
            return False
        arr_a = np.frombuffer(a.samples, dtype=np.uint8)
        arr_b = np.frombuffer(b.samples, dtype=np.uint8)
        if arr_a.shape != arr_b.shape:
            return False
        delta = np.abs(arr_a.astype(np.int16) - arr_b.astype(np.int16))
        if float(np.max(delta)) >= _HASH_MAX_THRESHOLD:
            return False
        return float(np.mean(delta)) < _HASH_MEAN_THRESHOLD
    except Exception:
        return False


def _warp(img: np.ndarray, H: np.ndarray, dsize: tuple[int, int]) -> np.ndarray:
    import cv2
    return cv2.warpPerspective(
        img, H, dsize,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )


def _diff_mask(old_img: np.ndarray, warped_new: np.ndarray) -> np.ndarray:
    import cv2
    diff = cv2.absdiff(old_img, warped_new)
    _, thresh = cv2.threshold(diff, _PIXEL_TOLERANCE, 255, cv2.THRESH_BINARY)
    # Close gaps so adjacent diff pixels merge into clusters.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
    return closed


def _cluster_mask(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Return list of (x, y, w, h, area) sorted by area desc."""
    import cv2
    n_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out: list[tuple[int, int, int, int, int]] = []
    for i in range(1, n_labels):
        x, y, w, h, area = stats[i]
        if w < _MIN_CLUSTER_PX or h < _MIN_CLUSTER_PX:
            continue
        out.append((int(x), int(y), int(w), int(h), int(area)))
    out.sort(key=lambda r: r[4], reverse=True)
    return out


def _crop_png(page: fitz.Page, pdf_rect: fitz.Rect, dpi: int = 150) -> bytes:
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    clipped = pdf_rect & page.mediabox
    if clipped.is_empty:
        return b""
    pix = page.get_pixmap(matrix=mat, clip=clipped, alpha=False)
    return pix.tobytes("png")


# ── Partial re-extraction helper ────────────────────────────────────────

def merge_partial_rerun(
    cached_rows: list,
    new_rows_for_changed: list,
    *,
    changed_sheet_ids: set[str],
):
    """Drop rows whose ``source_sheet`` is in ``changed_sheet_ids`` and
    splice in the freshly extracted ones from the revised PDF.

    Returns a new list (cached_rows is left untouched).
    """
    survivors = [
        r for r in cached_rows
        if (getattr(r, "source_sheet", "") or f"page-{getattr(r, 'source_page', 0)}")
        not in changed_sheet_ids
    ]
    return survivors + list(new_rows_for_changed)
