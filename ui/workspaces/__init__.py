"""Workspace widgets — full-screen tabs that compose the main window.

Each workspace combines panels and components into a coherent view
(takeoff / diff / cockpit / coverage). The main window's tab bar
swaps between them. Workspaces own their layout state — splitters,
filter bar selections, etc.
"""
from __future__ import annotations

from .takeoff_workspace import TakeoffWorkspace

__all__ = ["TakeoffWorkspace"]
