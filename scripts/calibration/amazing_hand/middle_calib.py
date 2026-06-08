"""
AmazingHand Middle Position Calibration Script

This script is used during Step 3 of the calibration process to fine-tune the
middle positions of a specific finger's servos. It cycles the chosen finger
between closed and open poses, and interactively prompts you to adjust the
offset (in degrees) for each servo. This compensates for slight misalignments
due to the discrete teeth on the servo splines. Adjusted values are saved to
'hand_calib_values.yaml'.
"""

import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_hand_calibration, load_hand_config, save_hand_calibration
from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand import compose_finger, degrees_to_servo_radians

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "hand_calib_values.yaml"
HAND_CONFIG_PATH = SCRIPT_DIR.parents[2] / "src" / "arm101_hand" / "data" / "hand_config.yaml"

VALID_FINGERS = ("index", "middle", "ring", "thumb")
EXIT_TOKENS = ("save", "q", "quit")


def prompt_finger():
    while True:
        choice = input(f"Which finger to calibrate? ({'/'.join(VALID_FINGERS)}): ").strip().lower()
        if choice in VALID_FINGERS:
            return choice
        print(f"  invalid -- pick one of {VALID_FINGERS}")


def prompt_int(label, current):
    while True:
        raw = input(f"New {label} [current={current}] (Enter to keep, 'save'/'q' to exit): ").strip()
        if raw == "":
            return current, False
        if raw.lower() in EXIT_TOKENS:
            return current, True
        try:
            return int(raw), False
        except ValueError:
            print("  invalid -- enter an integer, Enter to keep, or 'save'/'q' to exit")


def _send_pose(c, id1, id2, mp1, mp2, base, side, speed):
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    c.write_goal_speed(id2, speed)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))
    time.sleep(0.01)


def close_finger(c, id1, id2, mp1, mp2, limits, speed):
    _send_pose(c, id1, id2, mp1, mp2, limits.base_max, 0, speed)


def open_finger(c, id1, id2, mp1, mp2, limits, speed):
    _send_pose(c, id1, id2, mp1, mp2, limits.base_min, 0, speed)


def main():
    cfg = load_hand_calibration(YAML_PATH)
    hcfg = load_hand_config(HAND_CONFIG_PATH)
    finger = prompt_finger()
    block = cfg.fingers[finger]
    id1, id2 = FINGER_SERVO_IDS[finger]
    mp1 = block.servo_1.middle_pos
    mp2 = block.servo_2.middle_pos
    limits = block.limits
    speed = hcfg.tuning.speed

    c = Scs0009PyController(
        serial_port=hcfg.connection.port,
        baudrate=hcfg.connection.baudrate,
        timeout=hcfg.connection.timeout,
    )
    c.write_torque_enable(id1, 1)
    c.write_torque_enable(id2, 1)

    try:
        while True:
            print(f"[finger={finger}, ID_1={id1}, ID_2={id2}] MiddlePos_1={mp1}, MiddlePos_2={mp2}")
            close_finger(c, id1, id2, mp1, mp2, limits, speed)
            time.sleep(3)
            open_finger(c, id1, id2, mp1, mp2, limits, speed)
            time.sleep(1)

            new_mp1, exit_requested = prompt_int("MiddlePos_1", mp1)
            if exit_requested:
                break
            mp1 = new_mp1

            new_mp2, exit_requested = prompt_int("MiddlePos_2", mp2)
            if exit_requested:
                break
            mp2 = new_mp2
    except KeyboardInterrupt:
        print("\n^C -- saving and exiting")
    finally:
        block.servo_1.middle_pos = mp1
        block.servo_2.middle_pos = mp2
        save_hand_calibration(YAML_PATH, cfg)
        print(f"Saved to {YAML_PATH}")
        try:
            c.write_torque_enable(id1, 0)
            c.write_torque_enable(id2, 0)
        except Exception as e:
            print(f"warning: failed to disable torque: {e}")


if __name__ == "__main__":
    main()
