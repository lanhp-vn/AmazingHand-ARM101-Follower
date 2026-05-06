"""``arm101-gui`` console-script entry. Loads ``data/app_config.yaml``, builds
the QApplication, shows the main window, returns the exit code.

Per ``02-code-style-python.md`` §1 / §6: fail fast on startup if the YAML
is missing or fails validation; degrade in the loop later.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from arm101_hand.config import AppConfig, load_app_config

log = logging.getLogger(__name__)

# Repo root: <root>/src/arm101_hand/gui/app.py → parents[3] = <root>
_REPO_ROOT = Path(__file__).resolve().parents[3]
APP_CONFIG_PATH = _REPO_ROOT / "data" / "app_config.yaml"


def _load_config_or_exit() -> AppConfig:
    try:
        cfg = load_app_config(APP_CONFIG_PATH)
    except FileNotFoundError:
        print(f"app_config.yaml missing at {APP_CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    except ValidationError as e:
        print(f"app_config.yaml failed validation:\n{e}", file=sys.stderr)
        sys.exit(1)
    return cfg


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = _load_config_or_exit()

    # Lazy import: keeps `--help` / config-validation paths fast and lets
    # tests exercise `_load_config_or_exit` without a Qt event loop.
    from PySide6.QtWidgets import QApplication

    from arm101_hand.gui.main_window import MainWindow

    qt_app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(cfg)
    window.show()
    return int(qt_app.exec())


if __name__ == "__main__":
    sys.exit(main())
