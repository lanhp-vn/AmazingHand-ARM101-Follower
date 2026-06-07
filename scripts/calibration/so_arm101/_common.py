"""Shared setup glue for the SO-ARM101 verify/audit helper scripts.

Not pure (loads config + constructs the robot/bus), so the unit-testable calibration
math lives in ``arm101_hand.robots.calibration_summary`` instead. Kept thin.

IL-3: motors 1-5 only (no gripper ID 6). IL-4: callers own the bus for the duration
of one script run. IL-5: reads ``so101_follower.json``; never writes it.
"""

from __future__ import annotations

from pathlib import Path

from arm101_hand.config import load_app_config, load_arm_poses
from arm101_hand.robots.calibration_summary import ARM_JOINTS

# scripts/calibration/so_arm101/_common.py -> repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
APP_CONFIG_PATH = _REPO_ROOT / "data" / "app_config.yaml"
ARM_CONFIG_PATH = _REPO_ROOT / "data" / "arm_config.yaml"
CALIB_PATH = Path(__file__).resolve().parent / "so101_follower.json"

# id="so101_follower" -> SO101FollowerNoGripper loads <calibration_dir>/so101_follower.json,
# and the subclass defaults calibration_dir to this directory.
FOLLOWER_ID = "so101_follower"


def load_arm_app_config():
    """Load + validate data/app_config.yaml (SystemExit with a clear message if absent)."""
    if not APP_CONFIG_PATH.is_file():
        raise SystemExit(f"app_config.yaml not found at {APP_CONFIG_PATH}")
    return load_app_config(APP_CONFIG_PATH)


def build_follower(cfg, *, use_degrees: bool):
    """Construct an (unconnected) SO101FollowerNoGripper from ``cfg``.

    use_degrees=True -> DEGREES norm mode (poses/display);
    use_degrees=False -> RANGE_M100_100 (endpoint sweep, endpoints = +/-100).
    """
    from arm101_hand.robots import SO101FollowerNoGripper, SO101FollowerNoGripperConfig

    robot_cfg = SO101FollowerNoGripperConfig(
        port=cfg.arm.port,
        id=FOLLOWER_ID,
        use_degrees=use_degrees,
    )
    return SO101FollowerNoGripper(robot_cfg)


def build_raw_bus(cfg):
    """Construct an (unconnected) raw 5-motor FeetechMotorsBus for read-only scanning."""
    from lerobot.motors import Motor, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus

    motors = {
        name: Motor(motor_id, "sts3215", MotorNormMode.DEGREES)
        for motor_id, name in enumerate(ARM_JOINTS, start=1)
    }
    return FeetechMotorsBus(port=cfg.arm.port, motors=motors)


def gentle_velocity(cfg) -> int:
    """Goal_Velocity (STS3215 register 46, raw units) for gentle calibration moves."""
    return cfg.safety.safe_park.park_velocity_arm


def load_home_degrees() -> dict[str, float]:
    """Per-joint default-home target in degrees, from arm_config.yaml ``poses['home']``.

    This is the pose the motion scripts return to before releasing torque. Falls back to
    all-zeros (the calibrated mid) if the file or the ``home`` pose is absent.
    """
    if ARM_CONFIG_PATH.is_file():
        home = load_arm_poses(ARM_CONFIG_PATH).poses.get("home")
        if home is not None:
            return home.as_dict()
    return dict.fromkeys(ARM_JOINTS, 0.0)


def confirm_and_release(follower, torque_on: bool) -> None:
    """On exit, release torque WITHOUT auto-homing, after a reminder + confirmation.

    No script returns the arm to home on quit (that would be a surprise movement). Instead,
    if the arm is still holding a pose under torque, remind the operator that it will sag
    under gravity when released and wait for an explicit Enter, so they can move it to home /
    a resting pose (or brace it) by hand first. Ctrl+C / EOF at the prompt releases anyway
    (IL-4: torque must come off on exit). If torque is already off, just ensures it is off.
    """
    if torque_on:
        print(
            "\nReminder: the arm is holding its pose under torque and will SAG under gravity "
            "when released -- there is no auto-home. Move it to home or a resting pose by hand "
            "(or brace it) now."
        )
        try:
            input("Press Enter to release torque... ")
        except (EOFError, KeyboardInterrupt):
            print()
    follower.bus.disable_torque()
