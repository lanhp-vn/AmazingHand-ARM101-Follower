"""Pydantic schema for ``data/app_config.yaml`` (runtime safety + connection settings).

Defaults match the seeded YAML so a ``AppConfig()`` instantiation produces the
same shape the scripts load at startup. Loaded once per run; never re-read
mid-run (per ``02-code-style-python.md`` §6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class HandBus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: str = "COM18"
    baudrate: int = Field(default=1_000_000, ge=9600)
    timeout: float = Field(default=0.5, ge=0.0, le=5.0)
    default_speed: int = Field(default=3, ge=1, le=5)


class ArmBus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: str = "COM20"
    baudrate: int = Field(default=1_000_000, ge=9600)
    velocity_profile_default: int = Field(default=1500, ge=0, le=4095)


class WindowState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=1280, ge=640)
    height: int = Field(default=800, ge=480)
    active_tab: Literal["hand", "arm"] = "hand"
    log_panel_visible: bool = False


class SafePark(BaseModel):
    """Safe-park-then-disable settings (spec §7.5)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    arm_pose: str = "home"
    hand_pose: str = "middle"
    park_velocity_arm: int = Field(default=600, ge=0, le=4095)
    park_velocity_hand: int = Field(default=2, ge=1, le=5)
    park_timeout_s: float = Field(default=4.0, gt=0.0, le=30.0)
    arrival_tolerance_deg: float = Field(default=2.0, gt=0.0, le=20.0)


class Safety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_rate_hz: float = Field(default=1.0, gt=0.0, le=20.0)
    poller_enabled: bool = True
    temp_warn_c: float = Field(default=50.0, ge=0.0, le=100.0)
    temp_critical_c: float = Field(default=65.0, ge=0.0, le=100.0)
    voltage_warn_pct: float = Field(default=5.0, ge=0.0, le=100.0)
    voltage_critical_pct: float = Field(default=10.0, ge=0.0, le=100.0)
    auto_disable_torque_on_critical_temp: bool = True
    safe_park: SafePark = Field(default_factory=SafePark)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    hand: HandBus = Field(default_factory=HandBus)
    arm: ArmBus = Field(default_factory=ArmBus)
    window: WindowState = Field(default_factory=WindowState)
    safety: Safety = Field(default_factory=Safety)


def load_app_config(path: Path) -> AppConfig:
    """Parse and validate ``data/app_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)
