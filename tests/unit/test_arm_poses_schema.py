"""Schema tests for ``arm101_hand.config.arm_poses``."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import ARM_MOTORS, ArmPoseConfig, load_arm_poses

REPO_ROOT = Path(__file__).resolve().parents[2]
SEEDED_PATH = REPO_ROOT / "data" / "arm_config.yaml"


def test_seeded_yaml_loads_clean() -> None:
    cfg = load_arm_poses(SEEDED_PATH)
    assert cfg.schema_version == 1, "schema_version of seeded YAML"
    assert {"zero", "home", "rest"} <= set(cfg.quick_poses.keys()), (
        "seeded YAML has zero/home/rest quick-poses"
    )
    rest = cfg.quick_poses["rest"]
    assert rest.shoulder_lift == -90, "rest pose folds shoulder back (default safe-park target)"


def test_arm_motors_constant_is_canonical() -> None:
    """IL-3: motor names match SO101FollowerNoGripper's bus dict."""
    expected = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
    assert expected == ARM_MOTORS, "ARM_MOTORS must match the canon order"


def test_pose_with_all_motors_accepted() -> None:
    cfg = ArmPoseConfig.model_validate(
        {
            "poses": {
                "p1": {
                    "shoulder_pan": 10.0,
                    "shoulder_lift": -30,
                    "elbow_flex": 45,
                    "wrist_flex": -5,
                    "wrist_roll": 0,
                }
            }
        }
    )
    assert cfg.poses["p1"].as_dict()["shoulder_pan"] == 10.0, "as_dict round-trips a value"
    assert set(cfg.poses["p1"].as_dict().keys()) == set(ARM_MOTORS), "as_dict exposes all five motor names"


# | missing_field | description                                |
@pytest.mark.parametrize(
    "missing_field,desc",
    [
        ("shoulder_pan", "missing shoulder_pan rejected"),
        ("wrist_roll", "missing wrist_roll rejected"),
        ("elbow_flex", "missing elbow_flex rejected"),
    ],
)
def test_pose_missing_motor_rejected(missing_field: str, desc: str) -> None:
    full: dict[str, float] = dict.fromkeys(ARM_MOTORS, 0.0)
    full.pop(missing_field)
    with pytest.raises(ValidationError):
        ArmPoseConfig.model_validate({"poses": {"p1": full}})


def test_pose_extra_field_rejected() -> None:
    full: dict[str, float] = dict.fromkeys(ARM_MOTORS, 0.0)
    full["wrist_yaw"] = 99.0  # nonexistent motor
    with pytest.raises(ValidationError):
        ArmPoseConfig.model_validate({"poses": {"p1": full}})


def test_empty_quick_poses_and_poses_accepted() -> None:
    cfg = ArmPoseConfig.model_validate({"schema_version": 1, "quick_poses": {}, "poses": {}})
    assert cfg.quick_poses == {} and cfg.poses == {}, "both pose dicts may be empty"
