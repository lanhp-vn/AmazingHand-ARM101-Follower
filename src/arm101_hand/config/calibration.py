"""Pydantic schema for ``scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml``.

Read-only consumer — the calibration scripts under
``scripts/calibration/AmazingHand/`` own writing this file. The GUI imports
the resulting per-servo ``middle_pos`` values for calibration-aware slider
math (see ``hand/kinematics.degrees_to_servo_radians``).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

# Canonical finger labels, per IL-3. The GUI displays "Pointer" instead of
# "index", but the schema-level name stays "index".
FINGER_NAMES = ("index", "middle", "ring", "thumb")


class ServoCalibration(BaseModel):
    """One SCS0009 servo's calibrated neutral."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1, le=8)
    middle_pos: float


class FingerCalibration(BaseModel):
    """The two SCS0009 servos that drive one AmazingHand finger."""

    model_config = ConfigDict(extra="forbid")

    servo_1: ServoCalibration
    servo_2: ServoCalibration


class HandCalibration(BaseModel):
    """Top-level shape of ``AmazingHand_calib_values.yaml``."""

    model_config = ConfigDict(extra="forbid")

    com_port: str
    baudrate: int = Field(ge=9600)
    timeout: float = Field(ge=0.0, le=5.0)
    speed: int = Field(ge=1, le=7)
    fingers: dict[str, FingerCalibration]

    def middle_pos_by_id(self) -> dict[int, float]:
        """Flat ``{servo_id: middle_pos}`` lookup for the controller layer."""
        out: dict[int, float] = {}
        for finger in self.fingers.values():
            out[finger.servo_1.id] = finger.servo_1.middle_pos
            out[finger.servo_2.id] = finger.servo_2.middle_pos
        return out


def load_hand_calibration(path: Path) -> HandCalibration:
    """Parse and validate the AmazingHand calibration YAML."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return HandCalibration.model_validate(raw)
