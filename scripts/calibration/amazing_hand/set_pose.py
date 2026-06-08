"""
AmazingHand Set-Pose Utility

Drives the whole 4-finger AmazingHand to a single static pose and holds it there
under torque until you press Enter, then releases. Unlike the test scripts it
does not cycle; it just parks the hand in one position.

Two pose sources are accepted (resolved by ``arm101_hand.hand.resolve_hand_pose_targets``):

  * Built-in ``open`` / ``close`` -- every finger at ``base_min + margin`` / ``base_max - margin``
    (neutral spread). The margin backs each pose off the calibrated endpoints
    (measured at Step 4 with ``range_calib.py``) so the fingers never slam the
    measured limits while still staying inside the range. Margin is read from
    ``hand_config.yaml`` (``tuning.pose_margin_deg``).
  * Any named pose stored in ``src/arm101_hand/data/hand_config.yaml`` (e.g.
    ``grab``, ``middle``, saved via ``jog.py``).

Speeds: built-in open/close use ``tuning.speeds`` from ``hand_config.yaml``;
named poses use the default jog ``tuning.speed``.

Usage:
  uv run python scripts/calibration/amazing_hand/set_pose.py open
  uv run python scripts/calibration/amazing_hand/set_pose.py close
  uv run python scripts/calibration/amazing_hand/set_pose.py grab
  uv run python scripts/calibration/amazing_hand/set_pose.py        # prompts
"""

import contextlib
import sys
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import HandConfig, load_hand_calibration, load_hand_config
from arm101_hand.hand import BUILTIN_POSES, available_pose_names, resolve_hand_pose_targets
from arm101_hand.hand.protocol import SERVO_SYNC_S

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "hand_calib_values.yaml"
# scripts/calibration/amazing_hand/set_pose.py -> repo root is parents[2].
HAND_CONFIG_PATH = SCRIPT_DIR.parents[2] / "src" / "arm101_hand" / "data" / "hand_config.yaml"


def prompt_pose(valid):
    while True:
        choice = input(f"Pose? ({'/'.join(valid)}): ").strip().lower()
        if choice in valid:
            return choice
        print(f"  invalid -- pick one of {valid}")


def resolve_pose(argv, valid):
    """Pose from argv[1] if valid, else prompt. Warn on a bad explicit arg."""
    if len(argv) > 1:
        arg = argv[1].strip().lower()
        if arg in valid:
            return arg
        print(f"  '{argv[1]}' is not a valid pose -- pick one of {valid}")
    return prompt_pose(valid)


def drive_targets(c, targets, speed):
    """Command every servo to its wire-radians target at ``speed`` (torque already on)."""
    for sid in sorted(targets):
        c.write_goal_speed(sid, speed)
        time.sleep(SERVO_SYNC_S)
    for sid in sorted(targets):
        c.write_goal_position(sid, targets[sid])
        time.sleep(SERVO_SYNC_S)


def main():
    cfg = load_hand_calibration(YAML_PATH)
    hcfg: HandConfig = load_hand_config(HAND_CONFIG_PATH) if HAND_CONFIG_PATH.is_file() else HandConfig()

    valid = available_pose_names(hcfg)
    pose = resolve_pose(sys.argv, valid)

    targets = resolve_hand_pose_targets(cfg, hcfg, pose, margin_deg=hcfg.tuning.pose_margin_deg)
    if pose == "close":
        speed = hcfg.tuning.speeds.close
    elif pose == "open":
        speed = hcfg.tuning.speeds.open
    else:
        speed = hcfg.tuning.speed

    c = Scs0009PyController(
        serial_port=hcfg.connection.port,
        baudrate=hcfg.connection.baudrate,
        timeout=hcfg.connection.timeout,
    )

    for sid in targets:
        c.write_torque_enable(sid, 1)

    builtin = pose in BUILTIN_POSES
    detail = " (all fingers, neutral spread)" if builtin else ""
    print(f"Setting hand to '{pose}'{detail}...")
    try:
        drive_targets(c, targets, speed)
        print(f"Hand held at '{pose}' under torque.")
        input("Press Enter to release torque and exit... ")
    except KeyboardInterrupt:
        print("\n^C -- releasing")
    finally:
        for sid in targets:
            with contextlib.suppress(Exception):
                c.write_torque_enable(sid, 0)


if __name__ == "__main__":
    main()
