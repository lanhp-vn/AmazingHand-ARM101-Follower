"""Pydantic schema for ``data/hand_config.yaml`` (poses + sequences).

Position units: degrees relative to each servo's calibrated ``middle_pos``,
even-ID values pre-inverted in the list (see spec §5.5). The schema only
verifies structural shape — 8 ints per pose, sequence steps are strings.
Range/calibration validation happens at the application layer where the
loaded ``HandCalibration`` is available.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

POSITIONS_LEN = 8


class HandPose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: list[int] = Field(min_length=POSITIONS_LEN, max_length=POSITIONS_LEN)


class HandSequence(BaseModel):
    """A named list of step strings; deeper parsing belongs to ``sequence_player``."""

    model_config = ConfigDict(extra="forbid")

    steps: list[str] = Field(min_length=1)

    @field_validator("steps")
    @classmethod
    def _steps_well_formed(cls, value: list[str]) -> list[str]:
        # Structural shape only — full parsing happens in ``gui/sequence_player.py``.
        # Accept either ``SLEEP:<n>s`` or ``<pose>:<csv>|<delay>s``.
        for step in value:
            if step.startswith("SLEEP:"):
                continue
            if ":" in step and "|" in step:
                continue
            raise ValueError(f"malformed sequence step: {step!r}")
        return value


class HandPoseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    poses: dict[str, HandPose] = Field(default_factory=dict)
    sequences: dict[str, HandSequence] = Field(default_factory=dict)


def load_hand_poses(path: Path) -> HandPoseConfig:
    """Parse and validate ``data/hand_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return HandPoseConfig.model_validate(raw)
