"""Pydantic schemas for the unified-GUI YAML files (primitive layer).

- ``app_config`` → ``data/app_config.yaml``
- ``hand_poses`` → ``data/hand_config.yaml``
- ``arm_poses`` → ``data/arm_config.yaml``
- ``calibration`` → ``scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml``
"""

from .app_config import AppConfig, load_app_config
from .arm_poses import ARM_MOTORS, ArmPose, ArmPoseConfig, load_arm_poses
from .calibration import FINGER_NAMES, HandCalibration, load_hand_calibration
from .hand_poses import POSITIONS_LEN, HandPose, HandPoseConfig, HandSequence, load_hand_poses

__all__ = [
    "ARM_MOTORS",
    "FINGER_NAMES",
    "POSITIONS_LEN",
    "AppConfig",
    "ArmPose",
    "ArmPoseConfig",
    "HandCalibration",
    "HandPose",
    "HandPoseConfig",
    "HandSequence",
    "load_app_config",
    "load_arm_poses",
    "load_hand_calibration",
    "load_hand_poses",
]
