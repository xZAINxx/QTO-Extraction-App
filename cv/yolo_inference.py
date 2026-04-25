"""YOLOv8 patch-based inference for architectural symbol detection.

The model is FloorPlanCAD-pretrained (downloaded by ``cv/download_weights.py``)
and detects classes like ``door``, ``window``, ``light``, ``drain``,
``hatch``. We run it on overlapping 640×640 patches and stitch with
class-aware NMS so a symbol straddling a tile edge isn't counted twice.

Ultralytics is imported lazily so the rest of the QTO pipeline (which
doesn't need torch) keeps cold-starting in <500 ms.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from cv.patch_utils import (
    PATCH_OVERLAP,
    PATCH_SIZE,
    iter_patches,
    nms_per_class,
    project_patch_box,
)


DEFAULT_WEIGHTS = Path(__file__).resolve().parent / "weights" / "floorplancad_general.pt"
DEFAULT_CONF = 0.35
DEFAULT_IOU = 0.5


@dataclass(frozen=True)
class Detection:
    """A single symbol detection in parent-image coordinates."""
    class_name: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class DetectionResult:
    """Aggregated output of a YOLO sweep."""
    counts: dict[str, int]
    detections: list[Detection]

    @property
    def total(self) -> int:
        return len(self.detections)


class YOLODetector:
    """Lazy-loaded singleton wrapper around an Ultralytics YOLO model.

    We hold a single model instance per weights-path so repeated page
    sweeps don't pay the load cost (~2 s the first time).

    The constructor itself is cheap; the heavy load happens on first
    ``infer`` call so importing :mod:`cv.yolo_inference` doesn't yank
    in torch unless someone actually runs detection.
    """
    _CACHE: dict[str, "YOLODetector"] = {}

    def __init__(self, weights_path: str | Path = DEFAULT_WEIGHTS):
        self._weights_path = str(weights_path)
        self._model = None

    @classmethod
    def get(cls, weights_path: str | Path = DEFAULT_WEIGHTS) -> "YOLODetector":
        key = str(weights_path)
        det = cls._CACHE.get(key)
        if det is None:
            det = cls(key)
            cls._CACHE[key] = det
        return det

    def _load(self):
        if self._model is not None:
            return
        path = Path(self._weights_path)
        if not path.exists():
            raise FileNotFoundError(
                f"YOLO weights not found at {path}. Run cv/download_weights.py first."
            )
        from ultralytics import YOLO  # heavy import, kept local
        self._model = YOLO(str(path))

    def infer(
        self,
        image: np.ndarray,
        conf_threshold: float = DEFAULT_CONF,
        iou_threshold: float = DEFAULT_IOU,
        classes: Optional[Iterable[str]] = None,
        patch_size: int = PATCH_SIZE,
        overlap: int = PATCH_OVERLAP,
    ) -> DetectionResult:
        """Run patch-based inference over a parent image.

        Parameters
        ----------
        image
            HW or HWC uint8 array (typically a high-DPI page render).
        classes
            Optional whitelist of class names. ``None`` keeps everything.

        Returns
        -------
        DetectionResult
            Per-class counts and stitched bounding boxes.
        """
        self._load()
        assert self._model is not None
        names = self._model.names if hasattr(self._model, "names") else {}

        all_boxes: list[tuple[float, float, float, float]] = []
        all_scores: list[float] = []
        all_classes: list[str] = []

        wanted = set(classes) if classes is not None else None

        for patch in iter_patches(image, patch_size=patch_size, overlap=overlap):
            results = self._model.predict(
                patch.image,
                conf=conf_threshold,
                iou=iou_threshold,
                verbose=False,
            )
            if not results:
                continue
            r = results[0]
            if r.boxes is None or len(r.boxes) == 0:
                continue
            xyxy = r.boxes.xyxy.cpu().numpy() if hasattr(r.boxes.xyxy, "cpu") else np.asarray(r.boxes.xyxy)
            confs = r.boxes.conf.cpu().numpy() if hasattr(r.boxes.conf, "cpu") else np.asarray(r.boxes.conf)
            cls_ids = r.boxes.cls.cpu().numpy() if hasattr(r.boxes.cls, "cpu") else np.asarray(r.boxes.cls)

            for (x0, y0, x1, y1), score, cls_id in zip(xyxy, confs, cls_ids):
                cls_name = names.get(int(cls_id), str(int(cls_id)))
                if wanted is not None and cls_name not in wanted:
                    continue
                gx0, gy0, gx1, gy1 = project_patch_box(patch, (float(x0), float(y0), float(x1), float(y1)))
                all_boxes.append((gx0, gy0, gx1, gy1))
                all_scores.append(float(score))
                all_classes.append(cls_name)

        keep = nms_per_class(all_boxes, all_scores, all_classes, iou_threshold)
        detections = [
            Detection(
                class_name=all_classes[i],
                score=all_scores[i],
                x0=all_boxes[i][0], y0=all_boxes[i][1],
                x1=all_boxes[i][2], y1=all_boxes[i][3],
            )
            for i in keep
        ]
        counts = dict(Counter(d.class_name for d in detections))
        return DetectionResult(counts=counts, detections=detections)


def detect_symbols(
    image: np.ndarray,
    weights_path: str | Path = DEFAULT_WEIGHTS,
    **kwargs,
) -> DetectionResult:
    """Convenience function — fetches the cached detector and runs inference."""
    return YOLODetector.get(weights_path).infer(image, **kwargs)
