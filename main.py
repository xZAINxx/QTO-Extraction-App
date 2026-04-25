"""Zeconic QTO Extraction Tool — entry point."""
import os
import sys
from pathlib import Path

import yaml

# Load .env from app directory before anything else
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            if _v and not os.environ.get(_k.strip()):
                os.environ[_k.strip()] = _v.strip()
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
    config = load_config()

    from ui.main_window import MainWindow
    window = MainWindow(config, str(APP_DIR))
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
