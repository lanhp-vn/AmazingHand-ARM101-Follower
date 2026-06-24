"""Shared setup glue for the SO-ARM101 verify/audit helper scripts.

Device/bus constructors, path constants, AND the exit motion helpers
(``confirm_and_release`` / ``_drive_home``) now live in
``arm101_hand.scripts.device_setup`` (shared with ``scripts/diagnostics/`` and
``scripts/demos/``) and are re-exported here so the arm scripts' ``from _common
import ...`` keeps working unchanged.

IL-3: motors 1-5 only (no gripper ID 6). IL-4: callers own the bus for the
duration of one script run. IL-5: reads ``so101_follower.json``; never writes it.
"""

from __future__ import annotations

from arm101_hand.robots.calibration_summary import ARM_JOINTS, STS3215_RESOLUTION
from arm101_hand.scripts.device_setup import (
    ARM_CONFIG_PATH,
    CALIB_PATH,
    FOLLOWER_ID,
    build_follower,
    build_raw_bus,
    confirm_and_release,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
    safe_enable_torque,
)

__all__ = [
    "ARM_CONFIG_PATH",
    "CALIB_PATH",
    "FOLLOWER_ID",
    "ARM_JOINTS",
    "STS3215_RESOLUTION",
    "build_follower",
    "build_raw_bus",
    "gentle_velocity",
    "load_arm_app_config",
    "load_home_degrees",
    "confirm_and_release",
    "safe_enable_torque",
]
