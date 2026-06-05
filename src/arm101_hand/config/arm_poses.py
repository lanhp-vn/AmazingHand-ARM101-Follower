"""Pydantic schema for ``data/arm_config.yaml`` (arm poses).

Joint values are degrees, with lerobot's ``use_degrees=True`` mode active. The
schema accepts any numeric value; runtime clamping against per-motor
``range_min_deg`` / ``range_max_deg`` (from the lerobot calibration JSON)
happens at the application layer.

Motor names match the canonical IL-3 ordering: shoulder_pan → wrist_roll.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

ARM_MOTORS: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)


class ArmPose(BaseModel):
    """One arm pose: degrees per motor, all five required."""

    model_config = ConfigDict(extra="forbid")

    shoulder_pan: float
    shoulder_lift: float
    elbow_flex: float
    wrist_flex: float
    wrist_roll: float

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class ArmPoseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    poses: dict[str, ArmPose] = Field(default_factory=dict)


def load_arm_poses(path: Path) -> ArmPoseConfig:
    """Parse and validate ``data/arm_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ArmPoseConfig.model_validate(raw)


_SAVE_HEADER = (
    "# data/arm_config.yaml -- arm poses (degrees per joint; lerobot use_degrees mode).\n"
    "# MACHINE-MANAGED: rewritten by jog.py ('s' to save); do not hand-edit\n"
    "# (comments are regenerated on save). 'home' is the default parking / safe-park pose.\n"
)


def save_arm_poses(path: Path, config: ArmPoseConfig) -> None:
    """Write an ``ArmPoseConfig`` to YAML atomically (tmp file + ``os.replace``).

    The file is machine-owned: a fixed header is regenerated each write and any prior
    comments are discarded. ``sort_keys=False`` keeps a stable field order.
    """
    payload = config.model_dump(mode="python")
    body = yaml.safe_dump(payload, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_SAVE_HEADER + body, encoding="utf-8")
    os.replace(tmp, path)
