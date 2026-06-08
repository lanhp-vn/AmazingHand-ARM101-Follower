"""Pydantic schemas for the runtime config + calibration YAML files (primitive layer).

- ``app_config`` → ``data/app_config.yaml``
- ``arm_config`` → ``data/arm_config.yaml``
- ``hand_config`` → ``src/arm101_hand/data/hand_config.yaml``
- ``calibration`` → ``scripts/calibration/amazing_hand/hand_calib_values.yaml``
"""

from .app_config import AppConfig, load_app_config

# NOTE (transition): arm_config.ArmPose is intentionally NOT imported here so the bare
# name ArmPose keeps resolving to the legacy arm_poses class that existing arm scripts
# still use. Once arm_poses.py is removed in the arm switch task, promote the new class
# and delete this note.
from .arm_config import ArmConfig, ArmConnection, ArmSafety, ArmTuning, load_arm_config, save_arm_config
from .arm_poses import ARM_MOTORS, ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses
from .calibration import (
    FINGER_NAMES,
    DofLimits,
    HandCalibration,
    load_hand_calibration,
    save_hand_calibration,
)
from .hand_config import (
    HandConfig,
    HandConnection,
    HandPose,
    HandSafety,
    HandSpeeds,
    HandTuning,
    load_hand_config,
    save_hand_config,
)
from .motor_ids import FINGER_SERVO_IDS

__all__ = [
    "ARM_MOTORS",
    "FINGER_NAMES",
    "FINGER_SERVO_IDS",
    "AppConfig",
    "ArmConfig",
    "ArmConnection",
    "ArmSafety",
    "ArmTuning",
    "DofLimits",
    "ArmPose",
    "ArmPoseConfig",
    "HandCalibration",
    "HandConfig",
    "HandConnection",
    "HandPose",
    "HandSafety",
    "HandSpeeds",
    "HandTuning",
    "load_app_config",
    "load_arm_config",
    "load_arm_poses",
    "save_arm_config",
    "save_arm_poses",
    "load_hand_calibration",
    "load_hand_config",
    "save_hand_calibration",
    "save_hand_config",
]
