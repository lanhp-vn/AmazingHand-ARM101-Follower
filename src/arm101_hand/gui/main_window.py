"""Main window shell: header (status badges + E-STOP), tab widget, activity log.

Per spec §4.3, the header is **outside** the tab widget so it stays visible
from any tab. ``Esc`` / ``Shift+Esc`` are window-level shortcuts that drive
the same soft / hard signals as clicking the button. In Milestone 1 there are
no controllers — pressing E-STOP only writes to the activity log, which lets
us verify the signal flow before any real bus is touched.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from arm101_hand.config import AppConfig
from arm101_hand.gui.widgets import ActivityLog, EStopButton, StatusBadge


class MainWindow(QMainWindow):
    """Top-level window. Holds the header, tab widget, and activity log."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self.setWindowTitle("arm101-gui — Unified Manual Control")
        self.resize(config.window.width, config.window.height)

        self._hand_badge = StatusBadge("Hand")
        self._arm_badge = StatusBadge("Arm")
        self._estop = EStopButton()
        self._tabs = QTabWidget()
        self._log = ActivityLog()

        self._build_layout()
        self._wire_estop()
        self._install_shortcuts()
        self._restore_window_state()

        self._log.append("arm101-gui started — Milestone 1 shell (no devices yet).", "info")

    def _build_layout(self) -> None:
        header = QHBoxLayout()
        header.addWidget(self._hand_badge)
        header.addSpacing(12)
        header.addWidget(self._arm_badge)
        header.addStretch(1)
        header.addWidget(self._estop)

        # Placeholder tabs — populated in M3 (hand) and M4 (arm).
        hand_placeholder = QLabel("Hand panel — coming in Milestone 3.")
        hand_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hand_placeholder.setStyleSheet("color: #888; font-size: 14px;")
        arm_placeholder = QLabel("Arm panel — coming in Milestone 4.")
        arm_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arm_placeholder.setStyleSheet("color: #888; font-size: 14px;")
        self._tabs.addTab(hand_placeholder, "Hand")
        self._tabs.addTab(arm_placeholder, "Arm")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(self._log)
        self.setCentralWidget(central)

    def _wire_estop(self) -> None:
        # In M1, E-STOP only logs. M2 adds the safe-park orchestrator;
        # M3/M4 connect real controllers to the same signals.
        self._estop.soft_pressed.connect(lambda: self._on_estop("soft"))
        self._estop.hard_pressed.connect(lambda: self._on_estop("hard"))

    def _install_shortcuts(self) -> None:
        soft = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        soft.setContext(Qt.ShortcutContext.WindowShortcut)
        soft.activated.connect(self._estop.trigger_soft)

        hard = QShortcut(QKeySequence("Shift+Esc"), self)
        hard.setContext(Qt.ShortcutContext.WindowShortcut)
        hard.activated.connect(self._estop.trigger_hard)

    def _restore_window_state(self) -> None:
        # Active tab: "hand" → 0, "arm" → 1.
        self._tabs.setCurrentIndex(0 if self._config.window.active_tab == "hand" else 1)
        self._log.set_visible(self._config.window.log_panel_visible)

    def _on_estop(self, mode: str) -> None:
        # No controllers wired yet — just log. Replaced in M2 by SafePark dispatch.
        self._log.set_visible(True)
        self._log.append(f"[E-STOP] {mode} pressed (no devices connected yet)", "warn")
