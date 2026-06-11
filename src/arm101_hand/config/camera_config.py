"""Pydantic schema for ``src/arm101_hand/data/camera_config.yaml`` (primitive layer).

Operator config for the Optomed Aurora over the Pictor Wi-Fi API: connection ports +
timeouts, and capture-loop tuning (hold, new-file wait, paths). Hand-editable; never
written by runtime code (IL-5). Press-depth bounds reuse ``index_toggle`` constants,
so they are deliberately absent here.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class CameraConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str | None = Field(default=None, description="static camera IP; null = broadcast discover")
    discovery_port: int = Field(default=3000, ge=1, le=65535, description="UDP discovery port")
    message_port: int = Field(default=8000, ge=1, le=65535, description="TCP message socket port")
    discover_timeout_s: float = Field(default=4.0, gt=0, description="UDP discovery reply wait (s)")
    connect_timeout_s: float = Field(default=4.0, gt=0, description="TCP connect timeout (s)")
    io_timeout_s: float = Field(default=8.0, gt=0, description="TCP send/recv timeout (s)")


class CameraCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hold_seconds: float = Field(default=3.0, ge=0, description="shutter hold dwell after press reaches")
    new_file_timeout_s: float = Field(default=20.0, gt=0, description="max wait for the capture to land")
    poll_s: float = Field(default=0.5, gt=0, description="filelist poll interval while waiting (s)")
    stable_polls: int = Field(
        default=1,
        ge=1,
        description="consecutive successful polls at one size before pulling (1 = download on first sighting)",
    )
    dcim_root: str = Field(default="\\DCIM", description="camera image root for filelist/diff")
    fundus_dir: str = Field(default="fundus_images", description="repo-relative save folder")


class CameraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    connection: CameraConnection = Field(default_factory=CameraConnection)
    capture: CameraCapture = Field(default_factory=CameraCapture)


def load_camera_config(path: Path) -> CameraConfig:
    """Parse and validate ``camera_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return CameraConfig.model_validate(raw)
