"""Unified manual-control GUI (PySide6). See docs/plans/01-unified-gui-spec.md.

Application layer. Imports from ``arm101_hand.config`` (primitive) and from
``arm101_hand.hand`` / ``arm101_hand.robots`` (device) only — never the other
direction (per ``docs/conventions/01-module-layering.md`` §2).
"""
