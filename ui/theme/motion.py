"""Animation helper — single chokepoint for every QPropertyAnimation.

Why centralise:
    * The redesign caps animation noise at MOTION_INTENSITY 6. Routing
      every fade/slide/pulse through one helper makes that easy to audit
      and easy to throttle (e.g. respect a future "reduce motion"
      accessibility setting).
    * Overlapping animations on the same widget cause stutter — the helper
      keeps a per-widget registry and cancels any in-flight animation on a
      widget before starting a new one.
    * QSS gradients do not animate. Components that need a moving gradient
      (Skeleton shimmer) drive a numeric Qt property through this helper
      and translate it inside ``paintEvent``.
"""
from __future__ import annotations

from typing import Literal
from weakref import WeakValueDictionary

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QObject,
)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget


_STANDARD_EASING = QEasingCurve.Type.OutCubic
_SPRING_EASING = QEasingCurve.Type.OutBack


class Animator:
    """Owns running animations so callers can fire-and-forget."""

    _running: WeakValueDictionary[int, QAbstractAnimation] = WeakValueDictionary()

    @classmethod
    def _stop_existing(cls, widget: QWidget) -> None:
        existing = cls._running.get(id(widget))
        if existing is not None and existing.state() == QAbstractAnimation.State.Running:
            existing.stop()

    @classmethod
    def _register(cls, widget: QWidget, anim: QAbstractAnimation) -> None:
        cls._running[id(widget)] = anim

    # --- opacity animations ---------------------------------------------------

    @staticmethod
    def _ensure_opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect:
        existing = widget.graphicsEffect()
        if isinstance(existing, QGraphicsOpacityEffect):
            return existing
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        return effect

    @classmethod
    def fade_in(cls, widget: QWidget, duration_ms: int = 200) -> QPropertyAnimation:
        cls._stop_existing(widget)
        effect = cls._ensure_opacity_effect(widget)
        effect.setOpacity(0.0)
        widget.show()
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(duration_ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(_STANDARD_EASING)
        cls._register(widget, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim

    @classmethod
    def fade_out(cls, widget: QWidget, duration_ms: int = 200) -> QPropertyAnimation:
        cls._stop_existing(widget)
        effect = cls._ensure_opacity_effect(widget)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(duration_ms)
        anim.setStartValue(effect.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(_STANDARD_EASING)
        cls._register(widget, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim

    # --- slide ---------------------------------------------------------------

    @classmethod
    def slide_in(
        cls,
        widget: QWidget,
        from_edge: Literal["left", "right", "top", "bottom"] = "right",
        duration_ms: int = 200,
    ) -> QPropertyAnimation:
        cls._stop_existing(widget)
        target_pos = widget.pos()
        offset = 32
        offsets = {
            "left": QPoint(target_pos.x() - offset, target_pos.y()),
            "right": QPoint(target_pos.x() + offset, target_pos.y()),
            "top": QPoint(target_pos.x(), target_pos.y() - offset),
            "bottom": QPoint(target_pos.x(), target_pos.y() + offset),
        }
        if from_edge not in offsets:
            raise ValueError(f"from_edge must be one of {list(offsets)}; got {from_edge!r}")
        widget.move(offsets[from_edge])
        widget.show()
        anim = QPropertyAnimation(widget, b"pos", widget)
        anim.setDuration(duration_ms)
        anim.setStartValue(offsets[from_edge])
        anim.setEndValue(target_pos)
        anim.setEasingCurve(_SPRING_EASING)
        cls._register(widget, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim

    # --- pulse ---------------------------------------------------------------

    @classmethod
    def pulse(cls, widget: QWidget, duration_ms: int = 400) -> QPropertyAnimation:
        cls._stop_existing(widget)
        effect = cls._ensure_opacity_effect(widget)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(duration_ms)
        anim.setKeyValueAt(0.0, 1.0)
        anim.setKeyValueAt(0.5, 0.4)
        anim.setKeyValueAt(1.0, 1.0)
        anim.setEasingCurve(_STANDARD_EASING)
        cls._register(widget, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim

    # --- generic numeric property -------------------------------------------

    @classmethod
    def animate_property(
        cls,
        target: QObject,
        prop_name: bytes,
        start: float,
        end: float,
        duration_ms: int = 200,
        loop_count: int = 1,
        easing: QEasingCurve.Type = _STANDARD_EASING,
    ) -> QPropertyAnimation:
        """Drive an arbitrary Qt-property through an animation curve.

        Used by the Skeleton shimmer to translate its gradient offset.
        """
        if isinstance(target, QWidget):
            cls._stop_existing(target)
        anim = QPropertyAnimation(target, prop_name, target)
        anim.setDuration(duration_ms)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(easing)
        anim.setLoopCount(loop_count)
        if isinstance(target, QWidget):
            cls._register(target, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim


__all__ = ["Animator"]
