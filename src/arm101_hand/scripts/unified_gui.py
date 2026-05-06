"""Console-script entry: launches the unified PySide6 GUI.

Wired via ``pyproject.toml`` ``[project.scripts]`` as ``arm101-gui``. All real
work happens in :mod:`arm101_hand.gui.app`; this module is a thin delegate so
the script entry stays importable without pulling in PySide6 unnecessarily.
"""

from __future__ import annotations

import sys

from arm101_hand.gui.app import main as _gui_main


def main() -> int:
    return _gui_main()


if __name__ == "__main__":
    sys.exit(main())
