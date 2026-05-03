"""UI controllers — long-lived QObject helpers shared across MainWindow variants.

Workers and binding controllers that the GUI delegates work to live here so
both the legacy ``ui.main_window.MainWindow`` and the new
``ui.views.main_window.MainWindow`` can import them from one place.
"""
