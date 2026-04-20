"""Zeconic QTO Extraction Tool — entry point."""
import os
import sys
from pathlib import Path

import yaml
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

APP_DIR = Path(__file__).resolve().parent


def load_config() -> dict:
    cfg_path = APP_DIR / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}

    # Resolve relative paths against app dir
    for key in ("output_dir", "cache_dir", "template_path"):
        if key in cfg and not Path(cfg[key]).is_absolute():
            cfg[key] = str(APP_DIR / cfg[key])

    # API key from env if not in config
    if not cfg.get("anthropic_api_key"):
        cfg["anthropic_api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")

    return cfg


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Zeconic QTO Tool")
    app.setOrganizationName("Zeconic")
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

    config = load_config()

    from ui.main_window import MainWindow
    window = MainWindow(config, str(APP_DIR))
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
