"""
AmazingHand Set-Pose Utility

Drives the whole 4-finger AmazingHand to a single static pose -- ``open`` (full
extension) or ``close`` (full flexion) -- and holds it there under torque until
you press Enter, then releases. Unlike the test scripts it does not cycle; it
just parks the hand in one position.

Both poses are taken from the per-finger ``limits`` in
``AmazingHand_calib_values.yaml`` (measured at Step 4 with
``AmazingHand_RangeCalib.py``):

  * open  -> every finger at ``base_min``, neutral spread (side = 0)
  * close -> every finger at ``base_max``, neutral spread (side = 0)

Usage:
  uv run python scripts/calibration/AmazingHand/AmazingHand_SetPose.py open
  uv run python scripts/calibration/AmazingHand/AmazingHand_SetPose.py close
  uv run python scripts/calibration/AmazingHand/AmazingHand_SetPose.py   # prompts
"""
import contextlib
import sys
import time
from pathlib import Path

import yaml
from rustypot import Scs0009PyController

from arm101_hand.hand import compose_finger, degrees_to_servo_radians

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

VALID_POSES = ("open", "close")

# Gentler speed when curling in (close) so the fingers settle softly against the
# flexion limit; quicker on the way out (open). Independent of the YAML 'speed'
# (which is the calibration jog speed).
OPEN_SPEED = 7
CLOSE_SPEED = 3


def prompt_pose():
    while True:
        choice = input(f"Pose? ({'/'.join(VALID_POSES)}): ").strip().lower()
        if choice in VALID_POSES:
            return choice
        print(f"  invalid -- pick one of {VALID_POSES}")


def resolve_pose(argv):
    """Pose from argv[1] if valid, else prompt. Warn on a bad explicit arg."""
    if len(argv) > 1:
        arg = argv[1].strip().lower()
        if arg in VALID_POSES:
            return arg
        print(f"  '{argv[1]}' is not a valid pose -- pick one of {VALID_POSES}")
    return prompt_pose()


def pose_base(limits, pose):
    """Target ``base`` for the pose; spread stays neutral (side = 0)."""
    return limits["base_max"] if pose == "close" else limits["base_min"]


def move_finger(c, block, base, side, speed):
    id1 = block["servo_1"]["id"]
    id2 = block["servo_2"]["id"]
    mp1 = block["servo_1"]["middle_pos"]
    mp2 = block["servo_2"]["middle_pos"]
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    time.sleep(0.0002)
    c.write_goal_speed(id2, speed)
    time.sleep(0.0002)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))
    time.sleep(0.005)


def main():
    pose = resolve_pose(sys.argv)
    speed = CLOSE_SPEED if pose == "close" else OPEN_SPEED

    with open(YAML_PATH) as f:
        config = yaml.safe_load(f)
    fingers = config["fingers"]

    c = Scs0009PyController(
        serial_port=config["com_port"],
        baudrate=config["baudrate"],
        timeout=config["timeout"],
    )

    for block in fingers.values():
        c.write_torque_enable(block["servo_1"]["id"], 1)
        c.write_torque_enable(block["servo_2"]["id"], 1)

    print(f"Setting hand to '{pose}' (all fingers, neutral spread)...")
    try:
        for block in fingers.values():
            move_finger(c, block, pose_base(block["limits"], pose), 0, speed)
        print(f"Hand held at '{pose}' under torque.")
        input("Press Enter to release torque and exit... ")
    except KeyboardInterrupt:
        print("\n^C -- releasing")
    finally:
        for block in fingers.values():
            for servo_key in ("servo_1", "servo_2"):
                with contextlib.suppress(Exception):
                    c.write_torque_enable(block[servo_key]["id"], 0)


if __name__ == "__main__":
    main()
