"""Pydantic schema for ``src/arm101_hand/data/hand_config.yaml``.

Single operator tuning surface for the AmazingHand: connection, diagnostics
safety thresholds, motion tuning knobs, and named poses grouped per finger
(``[servo_1, servo_2]`` servo-frame degrees rel. to each servo's calibrated
middle, even-ID pre-inverted). Measured calibration stays in hand_calib_values.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

_PAIR = Field(min_length=2, max_length=2)


class HandConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: str = Field(default="COM18", description="AmazingHand 5V bus COM port")
    baudrate: int = Field(default=1_000_000, ge=9600, description="SCS0009 bus baudrate")
    timeout: float = Field(default=0.5, ge=0.0, le=5.0, description="serial read timeout (s)")


class HandSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temp_warn_c: float = Field(default=50.0, ge=0.0, le=100.0, description="servo coil temp warn (°C)")
    voltage_warn_pct: float = Field(
        default=5.0, ge=0.0, le=100.0, description="warn when bus V deviates this % from 5V nominal"
    )


class HandSpeeds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open: int = Field(default=5, ge=1, le=7, description="extension speed")
    close: int = Field(default=3, ge=1, le=7, description="flexion speed")


class HandTuning(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed: int = Field(default=4, ge=1, le=7, description="jog speed (1-7)")
    speeds: HandSpeeds = Field(default_factory=HandSpeeds, description="SetPose open/close motion speeds")
    step_min: int = Field(default=1, ge=1, description="min jog step (deg)")
    step_max: int = Field(default=15, ge=1, description="max jog step (deg)")
    step_default: int = Field(default=5, ge=1, description="initial jog step (deg)")
    pose_margin_deg: float = Field(default=5.0, ge=0, description="open/close back-off from limits (deg)")
    pose_timeout_s: float = Field(
        default=2.0, gt=0, description="hand move position-poll timeout (s) before continuing"
    )
    pose_poll_s: float = Field(
        default=0.03, gt=0, description="hand move position-poll interval (s)"
    )
    load_warn_threshold: int = Field(default=80, ge=0, le=100, description="SCS0009 load %% duty warn")
    jog_base_min: float = Field(default=-60, description="range_calib base discovery floor (logical deg)")
    jog_base_max: float = Field(default=130, description="range_calib base discovery ceil (logical deg)")
    jog_side_min: float = Field(default=-60, description="range_calib side discovery floor (logical deg)")
    jog_side_max: float = Field(default=60, description="range_calib side discovery ceil (logical deg)")


class HandPose(BaseModel):
    """One pose: each finger's [servo_1, servo_2] servo-frame degrees."""

    model_config = ConfigDict(extra="forbid")
    index: list[int] = _PAIR
    middle: list[int] = _PAIR
    ring: list[int] = _PAIR
    thumb: list[int] = _PAIR

    def by_finger(self) -> dict[str, list[int]]:
        return {"index": self.index, "middle": self.middle, "ring": self.ring, "thumb": self.thumb}


class HandConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    connection: HandConnection = Field(default_factory=HandConnection)
    safety: HandSafety = Field(default_factory=HandSafety)
    tuning: HandTuning = Field(default_factory=HandTuning)
    poses: dict[str, HandPose] = Field(default_factory=dict)


def load_hand_config(path: Path) -> HandConfig:
    """Parse and validate ``hand_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return HandConfig.model_validate(raw)


_HAND_SAVE_HEADER = (
    "# hand_config.yaml -- AmazingHand operator config (single tuning surface).\n"
    "# connection/safety/tuning are hand-editable; 'poses' is written by jog.py.\n"
    "# Comments are regenerated on save -- see data/README.md for knob meanings.\n"
)


def save_hand_config(path: Path, config: HandConfig) -> None:
    """Write a ``HandConfig`` to YAML atomically (tmp + ``os.replace``)."""
    payload = config.model_dump(mode="python")
    body = yaml.safe_dump(payload, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_HAND_SAVE_HEADER + body, encoding="utf-8")
    os.replace(tmp, path)
