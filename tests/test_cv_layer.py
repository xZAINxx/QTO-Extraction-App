"""Phase 2 unit tests — CV patch utilities, template matcher, and detector wiring.

These tests stay deliberately torch-free: the YOLO inference module is
imported lazily, so we exercise everything *except* the actual model
weights. ``parser.symbol_detector`` is verified to fail closed (return
``[]``) when CV deps are missing.
"""
from __future__ import annotations

import numpy as np
import pytest

from cv.patch_utils import (
    PATCH_OVERLAP,
    PATCH_SIZE,
    iter_patches,
    nms,
    nms_per_class,
    project_patch_box,
)


# ── patch_utils ────────────────────────────────────────────────────────────


def test_iter_patches_covers_image_with_overlap():
    img = np.zeros((1500, 2000, 3), dtype=np.uint8)
    patches = list(iter_patches(img))
    assert len(patches) > 1
    # Last patch must reach the bottom-right corner.
    last = patches[-1]
    assert last.x1 == img.shape[1]
    assert last.y1 == img.shape[0]
    # Patches should overlap by at least PATCH_OVERLAP - 1 between neighbours.
    xs = sorted({p.x0 for p in patches})
    if len(xs) > 1:
        for a, b in zip(xs, xs[1:]):
            assert b - a <= PATCH_SIZE - PATCH_OVERLAP + 1


def test_iter_patches_small_image_yields_one_patch():
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    patches = list(iter_patches(img))
    assert len(patches) == 1
    p = patches[0]
    assert (p.x0, p.y0, p.x1, p.y1) == (0, 0, 100, 100)


def test_iter_patches_rejects_bad_overlap():
    with pytest.raises(ValueError):
        list(iter_patches(np.zeros((640, 640), dtype=np.uint8),
                          patch_size=640, overlap=640))


def test_project_patch_box_translates_correctly():
    img = np.zeros((1500, 2000, 3), dtype=np.uint8)
    patches = list(iter_patches(img))
    # Pick a patch that's not in the top-left corner.
    p = next(p for p in patches if p.x0 > 0 and p.y0 > 0)
    local = (10.0, 20.0, 100.0, 200.0)
    g = project_patch_box(p, local)
    assert g == (p.x0 + 10.0, p.y0 + 20.0, p.x0 + 100.0, p.y0 + 200.0)


# ── NMS ────────────────────────────────────────────────────────────────────


def test_nms_dedupes_overlapping_boxes():
    boxes = [
        (0, 0, 10, 10),
        (1, 1, 11, 11),    # ≥ 0.5 IoU with the first → suppressed
        (100, 100, 110, 110),  # disjoint → kept
    ]
    scores = [0.9, 0.8, 0.7]
    keep = nms(boxes, scores, iou_threshold=0.5)
    assert sorted(keep) == [0, 2]


def test_nms_per_class_keeps_different_classes_even_when_overlapping():
    boxes = [(0, 0, 10, 10), (1, 1, 9, 9)]
    scores = [0.9, 0.85]
    classes = ["door", "window"]
    keep = nms_per_class(boxes, scores, classes, iou_threshold=0.5)
    assert sorted(keep) == [0, 1]


def test_nms_empty_input_returns_empty():
    assert nms([], [], 0.5) == []


# ── Template matcher ──────────────────────────────────────────────────────


def test_template_matcher_finds_synthetic_pattern():
    """Place a 30×30 cross on a blank canvas at three known locations and
    verify multi-scale matching finds all three with >0.78 score."""
    pytest.importorskip("cv2")
    import cv2

    canvas = np.full((400, 600), 255, dtype=np.uint8)

    def stamp(cx, cy, size=15):
        cv2.line(canvas, (cx - size, cy), (cx + size, cy), 0, 2)
        cv2.line(canvas, (cx, cy - size), (cx, cy + size), 0, 2)

    stamp(100, 100)
    stamp(300, 200)
    stamp(500, 350)

    # Build the template from the first instance.
    template = canvas[80:120, 80:120].copy()

    from cv.template_matcher import match_multiscale
    matches = match_multiscale(canvas, template, threshold=0.85)
    # Three distinct locations after NMS.
    centers = sorted(((m.x0 + m.x1) // 2, (m.y0 + m.y1) // 2) for m in matches)
    assert len(centers) == 3
    # Each detected center should be within 5 px of an expected one.
    expected = [(100, 100), (300, 200), (500, 350)]
    for got, want in zip(centers, expected):
        assert abs(got[0] - want[0]) < 6 and abs(got[1] - want[1]) < 6


def test_template_matcher_skips_oversized_template():
    pytest.importorskip("cv2")
    img = np.zeros((100, 100), dtype=np.uint8)
    big = np.zeros((200, 200), dtype=np.uint8)
    from cv.template_matcher import match_template
    assert match_template(img, big) == []


# ── symbol_detector graceful degradation ──────────────────────────────────


def test_symbol_detector_returns_empty_when_no_plan_bodies():
    """No plan_body zones → no inference attempted, returns []."""
    from parser.symbol_detector import detect_symbols_in_zone
    from parser.zone_segmenter import SheetZones
    import fitz

    zones = SheetZones(page_num=1, page_rect=fitz.Rect(0, 0, 100, 100))
    out = detect_symbols_in_zone(page=None, zones=zones)  # type: ignore[arg-type]
    assert out == []


def test_symbol_detector_returns_empty_when_weights_missing(tmp_path):
    """Missing weights file → degrade silently (Phase 1 must keep working)."""
    from parser.symbol_detector import detect_symbols_in_zone
    from parser.zone_segmenter import SheetZones, Zone
    import fitz

    page_rect = fitz.Rect(0, 0, 1000, 800)
    zones = SheetZones(
        page_num=1,
        page_rect=page_rect,
        plan_bodies=[Zone(rect=fitz.Rect(50, 50, 950, 750), label="plan_body")],
    )
    fake_weights = tmp_path / "nope.pt"
    out = detect_symbols_in_zone(
        page=None,  # type: ignore[arg-type]
        zones=zones,
        weights_path=fake_weights,
    )
    assert out == []
