"""
AmazingHand Motor Reset Script (Pre-Horn Installation)

This script is used during Step 2 of the calibration process, BEFORE attaching
the servo horns. It prompts for a finger and drives both of its servos to their
electrical 0° (center) position. The motors will hold this position under torque,
allowing you to attach the servo horns in the correct neutral orientation.
It also resets the 'middle_pos' values for that finger to 0 in the YAML file.
"""

import contextlib
import time
from pathlib import Path

import numpy as np
from rustypot import Scs0009PyController

from arm101_hand.config import load_hand_calibration, load_hand_config, save_hand_calibration
from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand.protocol import SERVO_SYNC_S

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "hand_calib_values.yaml"
HAND_CONFIG_PATH = SCRIPT_DIR.parents[2] / "src" / "arm101_hand" / "data" / "hand_config.yaml"

VALID_FINGERS = ("index", "middle", "ring", "thumb")


WARNING = """
======================================================================
  MOTOR RESET -- PRE-HORN-INSTALLATION
----------------------------------------------------------------------
  This script drives a servo to its 0 deg (centre) position so the
  servo horn can be attached in the correct neutral orientation.

  BEFORE CONTINUING:
    * The servo horn MUST NOT be attached to the motor shaft yet.
    * The motor must be wired, powered, and addressable on the bus.
    * Nothing should be mechanically coupled to the shaft.

  The motor will hold position under torque while you install the
  horn.  Torque is released automatically after you confirm install.
======================================================================
"""


def prompt_yes():
    answer = input("Proceed with motor reset? Type 'yes' to continue: ").strip().lower()
    return answer == "yes"


def prompt_finger():
    while True:
        raw = input(f"Which finger to reset? ({'/'.join(VALID_FINGERS)}, 'q' to quit): ").strip().lower()
        if raw in ("q", "quit", "exit"):
            return None
        if raw in VALID_FINGERS:
            return raw
        print(f"  invalid -- pick one of {VALID_FINGERS} or 'q'")


def reset_motor(c, servo_id, speed):
    c.write_torque_enable(servo_id, 1)
    c.write_goal_speed(servo_id, speed)
    c.write_goal_position(servo_id, np.deg2rad(0))
    time.sleep(1.0)
    print(f"  motor {servo_id} at 0 deg -- torque ON, holding position")


def main():
    print(WARNING)
    if not prompt_yes():
        print("Aborted.")
        return

    cfg = load_hand_calibration(YAML_PATH)
    hcfg = load_hand_config(HAND_CONFIG_PATH)

    c = Scs0009PyController(
        serial_port=hcfg.connection.port,
        baudrate=hcfg.connection.baudrate,
        timeout=hcfg.connection.timeout,
    )
    speed = hcfg.tuning.speed

    touched = set()
    try:
        while True:
            finger = prompt_finger()
            if finger is None:
                break
            block = cfg.fingers[finger]
            id1, id2 = FINGER_SERVO_IDS[finger]
            print(f"\n--- {finger} finger: servo_1={id1}, servo_2={id2} ---")
            reset_motor(c, id1, speed)
            time.sleep(SERVO_SYNC_S)
            touched.add(id1)
            reset_motor(c, id2, speed)
            touched.add(id2)
            block.servo_1.middle_pos = 0
            block.servo_2.middle_pos = 0
            save_hand_calibration(YAML_PATH, cfg)
            print(f"  {finger}.middle_pos -> 0, YAML saved")
    except KeyboardInterrupt:
        print("\n^C -- exiting")
    finally:
        for sid in touched:
            with contextlib.suppress(Exception):
                c.write_torque_enable(sid, 0)


if __name__ == "__main__":
    main()
