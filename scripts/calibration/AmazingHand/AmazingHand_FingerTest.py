"""
AmazingHand Finger Test Script

This script allows you to audit the calibration of a single finger.
It prompts you to select a finger (index, middle, ring, or thumb), then
continuously walks that finger through its full calibrated envelope on BOTH
degrees of freedom:

  * flexion / extension (the ``base`` axis): full close <-> full open, and
  * abduction / adduction (the ``side`` axis): spread one way <-> the other,

returning through neutral between phases. Every endpoint is taken straight from
the per-finger ``limits`` in ``AmazingHand_calib_values.yaml`` (measured at
Step 4 with ``AmazingHand_RangeCalib.py``). It makes no changes to the
calibration values -- it only drives the finger so you can watch for clean
endpoints, no buzz, and a symmetric spread. Press Ctrl+C to stop.
"""
import time
from pathlib import Path

import yaml
from rustypot import Scs0009PyController

from arm101_hand.hand import compose_finger, degrees_to_servo_radians

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

VALID_FINGERS = ("index", "middle", "ring", "thumb")


def prompt_finger():
    while True:
        choice = input(f"Which finger to test? ({'/'.join(VALID_FINGERS)}): ").strip().lower()
        if choice in VALID_FINGERS:
            return choice
        print(f"  invalid -- pick one of {VALID_FINGERS}")


def _send_pose(c, id1, id2, mp1, mp2, base, side, speed):
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    c.write_goal_speed(id2, speed)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))
    time.sleep(0.01)


def build_sequence(limits):
    """Ordered (label, base, side, dwell_s) poses covering both DOF.

    Flexion is tested first at neutral spread, then abduction is tested at
    neutral flexion, with a brief return to neutral between the two phases.
    """
    return [
        ("close  (full flexion)", limits["base_max"], 0, 3.0),
        ("open   (full extension)", limits["base_min"], 0, 2.0),
        ("neutral", 0, 0, 1.0),
        ("abduct (spread +)", 0, limits["side_max"], 2.0),
        ("adduct (spread -)", 0, limits["side_min"], 2.0),
        ("neutral", 0, 0, 1.0),
    ]


def main():
    with open(YAML_PATH) as f:
        config = yaml.safe_load(f)

    finger = prompt_finger()
    block = config["fingers"][finger]
    id1 = block["servo_1"]["id"]
    id2 = block["servo_2"]["id"]
    mp1 = block["servo_1"]["middle_pos"]
    mp2 = block["servo_2"]["middle_pos"]
    limits = block["limits"]
    speed = config["speed"]

    sequence = build_sequence(limits)

    c = Scs0009PyController(
        serial_port=config["com_port"],
        baudrate=config["baudrate"],
        timeout=config["timeout"],
    )
    c.write_torque_enable(id1, 1)
    c.write_torque_enable(id2, 1)

    print(f"[finger={finger}, ID_1={id1}, ID_2={id2}] limits={limits}")
    print("cycling flexion + abduction (from calibrated limits) -- Ctrl+C to stop")

    try:
        while True:
            for label, base, side, dwell in sequence:
                print(f"  -> {label}  (base={base}, side={side})")
                _send_pose(c, id1, id2, mp1, mp2, base, side, speed)
                time.sleep(dwell)
    except KeyboardInterrupt:
        print("\n^C -- stopping")
    finally:
        try:
            c.write_torque_enable(id1, 0)
            c.write_torque_enable(id2, 0)
        except Exception as e:
            print(f"warning: failed to disable torque: {e}")


if __name__ == "__main__":
    main()
