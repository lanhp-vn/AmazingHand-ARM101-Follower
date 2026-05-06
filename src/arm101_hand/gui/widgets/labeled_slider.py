"""Composite: ``[name] [slider] [target] [live]`` row used by both panels.

Sliders set ``Qt.NoFocus`` so the panel-level ``keyPressEvent`` (in
``hand_panel.py`` / ``arm_panel.py``) sees arrow keys instead of the slider
intercepting them.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QSlider, QWidget


class LabeledSlider(QWidget):
    """Horizontal slider with a left label, a target readout, and a live readout."""

    value_changed = Signal(int)

    def __init__(
        self,
        name: str,
        minimum: int,
        maximum: int,
        initial: int = 0,
        units: str = "°",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._units = units

        self._name_label = QLabel(name)
        self._name_label.setMinimumWidth(110)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(minimum, maximum)
        self._slider.setValue(initial)
        self._slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._slider.valueChanged.connect(self._on_value_changed)

        self._target_label = QLabel(self._format_value(initial))
        self._target_label.setMinimumWidth(60)
        self._target_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._live_label = QLabel("live: —")
        self._live_label.setMinimumWidth(80)
        self._live_label.setStyleSheet("color: #888888;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self._name_label)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._target_label)
        layout.addWidget(self._live_label)

    def value(self) -> int:
        return self._slider.value()

    def set_value(self, v: int) -> None:
        """Set without re-emitting (blocks signals so hardware is not double-triggered)."""
        self._slider.blockSignals(True)
        self._slider.setValue(v)
        self._slider.blockSignals(False)
        self._target_label.setText(self._format_value(v))

    def set_live(self, v: float | None) -> None:
        if v is None:
            self._live_label.setText("live: —")
        else:
            self._live_label.setText(f"live: {v:.1f}{self._units}")

    def set_range(self, minimum: int, maximum: int) -> None:
        self._slider.setRange(minimum, maximum)

    def _on_value_changed(self, v: int) -> None:
        self._target_label.setText(self._format_value(v))
        self.value_changed.emit(v)

    def _format_value(self, v: int) -> str:
        return f"{v:+d}{self._units}"
