"""Pydantic schema for ``scripts/calibration/amazing_hand/hand_calib_values.yaml``.

Loads the YAML via :func:`load_hand_calibration` and writes it back atomically
via :func:`save_hand_calibration`. The calibration and diagnostic scripts consume
the per-servo ``middle_pos`` (for calibration-aware logical↔servo math, see
``hand/kinematics.degrees_to_servo_radians``) and the per-finger DOF ``limits``
(``base``/``side`` min/max, logical frame) that bound motion.

This file holds **measurement-only** values (middle_pos + limits). Connection
settings and speed/pose tuning live in ``hand_config.yaml`` (v3 schema). Motor
IDs come from :data:`FINGER_SERVO_IDS` (IL-3 code canon).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Re-export for back-compat — callers that did ``from arm101_hand.config.calibration import FINGER_NAMES``
# continue to work after the canonical definitions moved to motor_ids.
from arm101_hand.config.motor_ids import FINGER_NAMES, FINGER_SERVO_IDS  # noqa: F401


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
    """One SCS0009 servo's calibrated neutral (measurement-only; ID comes from FINGER_SERVO_IDS)."""

    model_config = ConfigDict(extra="forbid")

    middle_pos: float


class FingerCalibration(BaseModel):
    """The two SCS0009 servos that drive one AmazingHand finger, plus its limits."""

    model_config = ConfigDict(extra="forbid")

    servo_1: ServoCalibration
    servo_2: ServoCalibration
    limits: DofLimits


class HandCalibration(BaseModel):
    """Top-level shape of ``hand_calib_values.yaml`` (v3).

    Holds measurement-only values: per-servo ``middle_pos`` and per-finger DOF
    ``limits``. Connection settings (com_port/baudrate/timeout) and motion-speed
    knobs live in ``hand_config.yaml``. Motor IDs come from ``FINGER_SERVO_IDS``
    (IL-3 code canon) rather than being stored here.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=3)
    fingers: dict[str, FingerCalibration]

    def middle_pos_by_id(self) -> dict[int, float]:
        """Flat ``{servo_id: middle_pos}`` lookup; IDs from FINGER_SERVO_IDS (IL-3 canon)."""
        out: dict[int, float] = {}
        for name, finger in self.fingers.items():
            id1, id2 = FINGER_SERVO_IDS[name]
            out[id1] = finger.servo_1.middle_pos
            out[id2] = finger.servo_2.middle_pos
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
