"""Header status badge: connection state + voltage + max temperature.

Renders a one-line summary of one device. The widget itself does not classify
warn/critical thresholds; the caller (safety subsystem) computes severity and
calls ``set_state``.

Visual: ``Hand: ○ Disconnected`` (gray dot) | ``Hand: ● COM18 5.02 V 46°C max`` (green/yellow/red dot).
"""

from __future__ import annotations

from typing import Literal

from PySide6.QtWidgets import QLabel, QWidget

Severity = Literal["disconnected", "ok", "warn", "critical"]

_DOT_BY_SEVERITY: dict[Severity, str] = {
    "disconnected": "○",
    "ok": "●",
    "warn": "●",
    "critical": "●",
}

_COLOR_BY_SEVERITY: dict[Severity, str] = {
    "disconnected": "#888888",
    "ok": "#2ca02c",
    "warn": "#d4a017",
    "critical": "#cc1f1f",
}


class StatusBadge(QLabel):
    """One-line "<Device>: <dot> <port> <voltage> <temp>" status header."""

    def __init__(self, device_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device = device_label
        self._severity: Severity = "disconnected"
        self._port: str = ""
        self._voltage_v: float | None = None
        self._temp_c_max: float | None = None
        self.setMinimumWidth(280)
        self._refresh()

    def set_state(
        self,
        *,
        severity: Severity,
        port: str = "",
        voltage_v: float | None = None,
        temp_c_max: float | None = None,
    ) -> None:
        self._severity = severity
        self._port = port
        self._voltage_v = voltage_v
        self._temp_c_max = temp_c_max
        self._refresh()

    def _refresh(self) -> None:
        dot = _DOT_BY_SEVERITY[self._severity]
        color = _COLOR_BY_SEVERITY[self._severity]

        if self._severity == "disconnected":
            body = "Disconnected"
        else:
            parts: list[str] = []
            if self._port:
                parts.append(self._port)
            if self._voltage_v is not None:
                parts.append(f"{self._voltage_v:.2f} V")
            if self._temp_c_max is not None:
                parts.append(f"{self._temp_c_max:.0f}°C max")
            body = "  ".join(parts) or "Connected"

        # Use HTML so we can color the dot independently from the rest.
        self.setText(f"<b>{self._device}:</b> <span style='color:{color};font-size:14px'>{dot}</span> {body}")
        self.setTextFormat(self.textFormat().RichText)
