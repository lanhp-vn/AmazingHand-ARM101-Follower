"""Pydantic schema for ``scripts/calibration/amazing_hand/hand_calib_values.yaml``.

Loads the YAML via :func:`load_hand_calibration` and writes it back atomically
via :func:`save_hand_calibration`. The calibration and diagnostic scripts consume
the per-servo ``middle_pos`` (for calibration-aware logical↔servo math, see
``hand/kinematics.degrees_to_servo_radians``) and the per-finger DOF ``limits``
(``base``/``side`` min/max, logical frame) that bound motion.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Canonical finger labels, per IL-3. The first finger ("index") is the pointer
# finger; the schema-level name stays "index".
FINGER_NAMES = ("index", "middle", "ring", "thumb")


class PoseSpeeds(BaseModel):
    """SetPose's open/close motion speeds (1-7 scale), distinct from the jog ``speed``."""

    model_config = ConfigDict(extra="forbid")

    open: int = Field(default=5, ge=1, le=7)  # extension (quicker)
    close: int = Field(default=3, ge=1, le=7)  # flexion (gentler settle)


class DofLimits(BaseModel):
    """Per-finger motion envelope in the **logical** frame (degrees rel. to middle).

    ``base`` is flexion (positive = close); ``side`` is abduction/adduction.
    Matches ``hand.kinematics.compose_finger``'s ``base``/``side`` convention
    (``pos1 = base - side``, ``pos2 = base + side``, even-ID already inverted).
    """

    model_config = ConfigDict(extra="forbid")

    base_min: float  # full extension / open
    base_max: float  # full flexion / close
    side_min: float  # spread one way
    side_max: float  # spread the other

    @model_validator(mode="after")
    def _check_ordering(self) -> DofLimits:
        if self.base_min >= self.base_max:
            raise ValueError(f"base_min ({self.base_min}) must be < base_max ({self.base_max})")
        if self.side_min >= self.side_max:
            raise ValueError(f"side_min ({self.side_min}) must be < side_max ({self.side_max})")
        return self


class ServoCalibration(BaseModel):
    """One SCS0009 servo's calibrated neutral."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1, le=8)
    middle_pos: float


class FingerCalibration(BaseModel):
    """The two SCS0009 servos that drive one AmazingHand finger, plus its limits."""

    model_config = ConfigDict(extra="forbid")

    servo_1: ServoCalibration
    servo_2: ServoCalibration
    limits: DofLimits


class HandCalibration(BaseModel):
    """Top-level shape of ``hand_calib_values.yaml`` (v2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=2)
    com_port: str
    baudrate: int = Field(ge=9600)
    timeout: float = Field(ge=0.0, le=5.0)
    speed: int = Field(ge=1, le=7)
    speeds: PoseSpeeds = Field(default_factory=PoseSpeeds)
    fingers: dict[str, FingerCalibration]

    def middle_pos_by_id(self) -> dict[int, float]:
        """Flat ``{servo_id: middle_pos}`` lookup for the controller layer."""
        out: dict[int, float] = {}
        for finger in self.fingers.values():
            out[finger.servo_1.id] = finger.servo_1.middle_pos
            out[finger.servo_2.id] = finger.servo_2.middle_pos
        return out

    def limits_by_finger(self) -> dict[str, DofLimits]:
        """``{finger_name: DofLimits}`` lookup for kinematics + audit scripts."""
        return {name: finger.limits for name, finger in self.fingers.items()}


def load_hand_calibration(path: Path) -> HandCalibration:
    """Parse and validate the hand calibration YAML."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return HandCalibration.model_validate(raw)


def save_hand_calibration(path: Path, config: HandCalibration) -> None:
    """Write a ``HandCalibration`` to YAML atomically (tmp file + ``os.replace``).

    The whole model is dumped, so a load-modify-save round-trip preserves every field;
    the per-finger partial writes the calib scripts used to do by hand are unnecessary.
    Block-style YAML (no custom inline dumper) -- matches the arm's ``save_arm_poses``.
    """
    payload = config.model_dump(mode="python")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)
