"""View-layer composition root for the modern UI.

The old single-file ``ui/main_window.py`` housed the legacy 248-px sidebar
layout. Wave 3 introduces ``ui/views/main_window.py`` — the topbar +
nav-rail + workspace shell. Both windows still ship and ``main.py`` picks
between them via the ``ui_v2`` feature flag in ``config.yaml``.
"""
