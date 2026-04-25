"""Computer-vision layer for the QTO tool.

Heavy modules (yolo_inference) lazy-import their dependencies so the rest
of the pipeline keeps starting in <500 ms even when CV is disabled.
"""
from cv.patch_utils import (
    Patch,
    iter_patches,
    nms,
    nms_per_class,
    project_patch_box,
)
from cv.template_matcher import (
    TemplateMatch,
    match_multiscale,
    match_template,
)

__all__ = [
    "Patch",
    "TemplateMatch",
    "iter_patches",
    "match_multiscale",
    "match_template",
    "nms",
    "nms_per_class",
    "project_patch_box",
]
