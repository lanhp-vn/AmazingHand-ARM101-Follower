"""Grab demo: stage the SO-ARM101 into ``grab``, close the AmazingHand, hold both under
torque, and reverse the whole thing on exit.

The staged forward grab, the reverse-on-'h', and the connect/release live in
``arm101_hand.scripts.grab_common`` (shared with ``grab_toggle.py``); this script is the
plain entry point with no interactive hold.

Usage:
  uv run python scripts/demos/grab_sequence.py
"""

from __future__ import annotations

import sys

from arm101_hand.scripts.grab_common import run_grab_demo


def main() -> int:
    return run_grab_demo()


if __name__ == "__main__":
    sys.exit(main())
