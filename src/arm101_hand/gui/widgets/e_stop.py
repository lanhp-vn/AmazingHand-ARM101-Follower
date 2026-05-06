"""Large red E-STOP button (spec §4.3, §7.3).

Two click semantics:
- **Click** → soft (safe-park then disable, default).
- **Shift+click** → hard (instant ``disable_torque_all``, skips park step).

Triggered on ``mousePressEvent`` rather than ``clicked`` so any mouse-down
fires the stop immediately, even if the user drags off the button. The button
also responds to the global ``Esc`` / ``Shift+Esc`` shortcuts wired in the
main window — those go through the same signal slots.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QPushButton, QWidget


class EStopButton(QPushButton):
    """Top-right safety control. Emits ``soft_pressed`` or ``hard_pressed``."""

    soft_pressed = Signal()
    hard_pressed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("⛔ E-STOP", parent)
        self.setObjectName("EStopButton")
        self.setMinimumWidth(140)
        self.setMinimumHeight(40)
        self.setToolTip(
            "Click — soft E-STOP (safe-park then disable torque)\n"
            "Shift+Click — hard E-STOP (instant disable, skips park)"
        )
        self.setStyleSheet(
            "QPushButton#EStopButton {"
            "  background-color: #cc1f1f;"
            "  color: white;"
            "  font-weight: bold;"
            "  font-size: 13px;"
            "  border: 2px solid #800000;"
            "  border-radius: 6px;"
            "  padding: 4px 12px;"
            "}"
            "QPushButton#EStopButton:hover { background-color: #e64545; }"
            "QPushButton#EStopButton:pressed { background-color: #990000; }"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.hard_pressed.emit()
            else:
                self.soft_pressed.emit()
        super().mousePressEvent(event)

    def trigger_soft(self) -> None:
        """Programmatic soft E-STOP — used by the global ``Esc`` shortcut."""
        self.soft_pressed.emit()

    def trigger_hard(self) -> None:
        """Programmatic hard E-STOP — used by the global ``Shift+Esc`` shortcut."""
        self.hard_pressed.emit()
