"""Collapsible append-only activity log shared across both tabs.

A toggle button shows/hides a fixed-height read-only text view. Append takes
a severity ``level`` to colorize the line; the log itself does no thresholding.
Capped at ``MAX_LINES`` to bound memory.
"""

from __future__ import annotations

import time
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

Level = Literal["info", "warn", "error"]
MAX_LINES = 1000

_COLOR_BY_LEVEL: dict[Level, str] = {
    "info": "#dddddd",
    "warn": "#d4a017",
    "error": "#cc1f1f",
}


class ActivityLog(QWidget):
    """Collapsible activity log. Default collapsed (per ``app_config.window``)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._toggle = QPushButton("▶ Activity log")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(False)
        self._toggle.setFlat(True)
        self._toggle.setStyleSheet("text-align: left;")
        self._toggle.toggled.connect(self._on_toggle)

        self._view = QTextEdit()
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._view.setFixedHeight(140)  # ~8 lines
        self._view.setVisible(False)
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._view.setStyleSheet(
            "QTextEdit { background-color: #1e1e1e; color: #dddddd; "
            "font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._toggle, 1, Qt.AlignmentFlag.AlignLeft)
        layout.addLayout(toolbar)
        layout.addWidget(self._view)

        self._line_count = 0

    def append(self, message: str, level: Level = "info") -> None:
        ts = time.strftime("%H:%M:%S")
        color = _COLOR_BY_LEVEL[level]
        # Escape minimal HTML metacharacters; activity log content is internal.
        safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        line = f"<span style='color:#888'>{ts}</span> <span style='color:{color}'>{safe}</span>"
        self._view.append(line)
        self._line_count += 1
        if self._line_count > MAX_LINES:
            self._trim_to_max()

    def set_visible(self, visible: bool) -> None:
        """Programmatic open/close — used to restore window state on launch."""
        self._toggle.setChecked(visible)

    def is_open(self) -> bool:
        return self._toggle.isChecked()

    def _on_toggle(self, checked: bool) -> None:
        self._view.setVisible(checked)
        self._toggle.setText(("▼ " if checked else "▶ ") + "Activity log")

    def _trim_to_max(self) -> None:
        # Remove the oldest blocks down to MAX_LINES.
        doc = self._view.document()
        while doc.blockCount() > MAX_LINES:
            cursor = self._view.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()  # remove the leading newline
        self._line_count = doc.blockCount()
