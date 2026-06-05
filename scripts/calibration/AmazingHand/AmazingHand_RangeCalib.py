"""AmazingHand Range Calibration Script (Step 4) — arrow-key jog-to-limit.

Discovers per-finger DOF limits (logical-frame ``base``/``side`` min/max) on the
real hardware and writes them into ``AmazingHand_calib_values.yaml``. Run AFTER
middle-position calibration (Step 3).

Controls (torque ON the whole time):
  Up / Down     base + / -   (flex toward close / extend toward open)
  Right / Left  side + / -   (spread)
  [ / ]         shrink / grow the jog step
  1 2 3 4       mark base_min / base_max / side_min / side_max at the cursor
  h             home to (0, 0) = calibrated middle
  s             save this finger's limits to YAML
  q / Ctrl+C    save and exit (torque off)

Windows-only: uses ``msvcrt.getwch`` for raw key reads. The pure jog logic is in
``arm101_hand.hand.range_calib`` (unit-tested); this file only does I/O.
"""

import msvcrt
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import DofLimits, load_hand_calibration, save_hand_calibration
from arm101_hand.hand import (
    apply_action,
    compose_finger,
    degrees_to_servo_radians,
    format_status,
    key_to_action,
    load_warning,
)
from arm101_hand.hand.range_calib import JogState

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

VALID_FINGERS = ("index", "middle", "ring", "thumb")


def limits_error(limits):
    """Return an error string if the limit set violates v2 ordering, else None."""
    errs = []
    if limits["base_min"] >= limits["base_max"]:
        errs.append(f"base_min ({limits['base_min']}) >= base_max ({limits['base_max']})")
    if limits["side_min"] >= limits["side_max"]:
        errs.append(f"side_min ({limits['side_min']}) >= side_max ({limits['side_max']})")
    return "; ".join(errs) if errs else None


def prompt_finger():
    while True:
        choice = input(f"Which finger to range-calibrate? ({'/'.join(VALID_FINGERS)}): ").strip().lower()
        if choice in VALID_FINGERS:
            return choice
        print(f"  invalid -- pick one of {VALID_FINGERS}")


def read_key():
    """Block for one key; normalize arrows to UP/DOWN/LEFT/RIGHT tokens."""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix
        code = msvcrt.getwch()
        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(code, "")
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def write_cursor(c, id1, id2, mp1, mp2, state, speed):
    """Compose the (base, side) cursor and command both servos."""
    pos1, pos2 = compose_finger(state.base, state.side)
    c.write_goal_speed(id1, speed)
    c.write_goal_speed(id2, speed)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))


def read_loads(c, id1, id2):
    def _scalar(v):
        return int(v[0]) if isinstance(v, (list, tuple)) else int(v)

    return _scalar(c.read_present_load(id1)), _scalar(c.read_present_load(id2))


def main():
    cfg = load_hand_calibration(YAML_PATH)
    finger = prompt_finger()
    block = cfg.fingers[finger]
    id1 = block.servo_1.id
    id2 = block.servo_2.id
    mp1 = block.servo_1.middle_pos
    mp2 = block.servo_2.middle_pos
    speed = cfg.speed
    limits = block.limits.model_dump()  # working dict; start from current stored limits

    c = Scs0009PyController(
        serial_port=cfg.com_port,
        baudrate=cfg.baudrate,
        timeout=cfg.timeout,
    )
    c.write_torque_enable(id1, 1)
    c.write_torque_enable(id2, 1)

    state = JogState()
    print(__doc__)
    print(f"[finger={finger}, ID_1={id1}, ID_2={id2}] current limits: {limits}")
    write_cursor(c, id1, id2, mp1, mp2, state, speed)

    try:
        while True:
            key = read_key()
            action = key_to_action(key)
            if action is None:
                continue
            if action == "quit":
                break
            if action == "save":
                err = limits_error(limits)
                if err:
                    print(f"  NOT saved -- invalid limits: {err}")
                else:
                    block.limits = DofLimits(**limits)
                    save_hand_calibration(YAML_PATH, cfg)
                    print(f"  saved limits {limits} for {finger}")
                continue

            state, mark = apply_action(state, action)
            if mark is not None:
                name, value = mark
                limits[name] = value
                print(f"  marked {name} = {value}")
            else:
                write_cursor(c, id1, id2, mp1, mp2, state, speed)

            load1, load2 = read_loads(c, id1, id2)
            print("  " + format_status(state, load1, load2))
            warn = load_warning(load1, load2)
            if warn:
                print("  " + warn)
    except KeyboardInterrupt:
        print("\n^C -- saving and exiting")
    finally:
        err = limits_error(limits)
        if err:
            print(f"NOT saving -- invalid limits: {err}. Re-run and mark valid endpoints.")
        else:
            block.limits = DofLimits(**limits)
            save_hand_calibration(YAML_PATH, cfg)
            print(f"Saved limits for {finger} to {YAML_PATH}")
        for sid in (id1, id2):
            try:
                c.write_torque_enable(sid, 0)
            except Exception as e:
                print(f"warning: failed to disable torque on {sid}: {e}")


if __name__ == "__main__":
    main()
