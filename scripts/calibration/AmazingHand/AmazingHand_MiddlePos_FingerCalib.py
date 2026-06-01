"""
AmazingHand Middle Position Calibration Script

This script is used during Step 3 of the calibration process to fine-tune the
middle positions of a specific finger's servos. It cycles the chosen finger
between closed and open poses, and interactively prompts you to adjust the
offset (in degrees) for each servo. This compensates for slight misalignments
due to the discrete teeth on the servo splines. Adjusted values are saved to
'AmazingHand_calib_values.yaml'.
"""
import time
from pathlib import Path

import yaml
from rustypot import Scs0009PyController

from arm101_hand.hand import compose_finger, degrees_to_servo_radians

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

VALID_FINGERS = ("index", "middle", "ring", "thumb")
EXIT_TOKENS = ("save", "q", "quit")


class InlineDict(dict):
    pass


def _inline_dict_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True)


yaml.SafeDumper.add_representer(InlineDict, _inline_dict_representer)


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def save_config(path, data):
    out = {k: v for k, v in data.items() if k != "fingers"}
    out["fingers"] = {}
    for finger, block in data["fingers"].items():
        out["fingers"][finger] = {
            "servo_1": InlineDict(block["servo_1"]),
            "servo_2": InlineDict(block["servo_2"]),
            "limits": InlineDict(block["limits"]),
        }
    with open(path, "w") as f:
        yaml.safe_dump(out, f, sort_keys=False, default_flow_style=False)


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
    _send_pose(c, id1, id2, mp1, mp2, limits["base_max"], 0, speed)


def open_finger(c, id1, id2, mp1, mp2, limits, speed):
    _send_pose(c, id1, id2, mp1, mp2, limits["base_min"], 0, speed)


def main():
    config = load_config(YAML_PATH)
    finger = prompt_finger()
    block = config["fingers"][finger]
    id1 = block["servo_1"]["id"]
    id2 = block["servo_2"]["id"]
    mp1 = block["servo_1"]["middle_pos"]
    mp2 = block["servo_2"]["middle_pos"]
    limits = block["limits"]
    speed = config["speed"]

    c = Scs0009PyController(
        serial_port=config["com_port"],
        baudrate=config["baudrate"],
        timeout=config["timeout"],
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
        block["servo_1"]["middle_pos"] = mp1
        block["servo_2"]["middle_pos"] = mp2
        save_config(YAML_PATH, config)
        print(f"Saved to {YAML_PATH}")
        try:
            c.write_torque_enable(id1, 0)
            c.write_torque_enable(id2, 0)
        except Exception as e:
            print(f"warning: failed to disable torque: {e}")


if __name__ == "__main__":
    main()
