"""AmazingHand Jog & Save-Pose tool -- arrow-key jog of all fingers, then save.

Move all four fingers with the keyboard (torque ON the whole time), then save the
resulting whole-hand pose by name into ``src/arm101_hand/data/hand_config.yaml``.
Mirrors the arm's ``so_arm101/jog.py``.

Config (serial + per-finger middle_pos + limits) is read from the canonical
``hand_calib_values.yaml`` (IL-5: this script never writes it).
Connection, speed, and pose storage live in ``hand_config.yaml``.

Controls (torque ON the whole time):
  1 2 3 4       select active finger (index / middle / ring / thumb)
  Up / Down     active finger base + / -   (flex / extend)
  Right / Left  active finger side + / -   (spread)
  [ / ]         shrink / grow the jog step
  h             home the active finger to (0, 0) neutral
  H             home ALL fingers to (0, 0) neutral
  s             save the current whole-hand pose (prompts for a name)
  q / Ctrl+C    on quit, prompts: 'h' to home all fingers to neutral first, or Enter to
                release torque on all 8 servos and exit

On startup the cursor is initialized from the hand's CURRENT position (no auto-home) and
torque holds that pose without a jump; use ``h``/``H`` to home explicitly. Each finger's
cursor is clamped to its calibrated base/side limits, so you can only build poses inside
the known-good envelope. Windows-only: uses ``msvcrt`` for raw keys.
"""

import msvcrt
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import (
    HandConfig,
    HandPose,
    load_hand_calibration,
    load_hand_config,
    save_hand_config,
)
from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand import (
    compose_finger,
    decompose_finger,
    degrees_to_servo_radians,
    finger_positions_to_servo_frame,
    load_warning,
    servo_radians_to_degrees,
    validate_pose_name,
)
from arm101_hand.hand.pose_jog import (
    FINGERS,
    HandJogState,
    apply_action,
    format_hand_status,
    key_to_action,
)

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "hand_calib_values.yaml"
# scripts/calibration/amazing_hand/jog.py -> repo root is parents[2].
REPO_ROOT = SCRIPT_DIR.parents[2]
HAND_CONFIG_PATH = REPO_ROOT / "src" / "arm101_hand" / "data" / "hand_config.yaml"


def read_key():
    """Block for one key; normalize arrows to UP/DOWN/LEFT/RIGHT tokens."""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix
        code = msvcrt.getwch()
        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(code, "")
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def _scalar(v):
    """Unwrap rustypot's single-element list reads (``read_<reg>`` returns a list)."""
    return v[0] if isinstance(v, (list, tuple)) else v


def read_loads(c, id1, id2):
    return int(_scalar(c.read_present_load(id1))), int(_scalar(c.read_present_load(id2)))


def hold_present_and_read_state(c, cfg):
    """Hold the hand's CURRENT pose without a jump and seed the jog cursor from it.

    For each servo: read present position, command it as the goal, then enable torque
    (goal == present, so no motion -- mirrors the arm jog's ``_hold_at``). The cursor is
    the present pose decomposed to logical ``(base, side)`` per finger, clamped to each
    finger's calibrated limits, so jogging continues from where the hand already is
    instead of snapping every finger to neutral.
    """
    fingers = {}
    for name in FINGERS:
        block = cfg.fingers[name]
        lim = block.limits
        id1, id2 = FINGER_SERVO_IDS[name]
        rad1 = float(_scalar(c.read_present_position(id1)))
        rad2 = float(_scalar(c.read_present_position(id2)))
        # No-jump hold: command present, then enable torque.
        c.write_goal_position(id1, rad1)
        c.write_goal_position(id2, rad2)
        c.write_torque_enable(id1, 1)
        c.write_torque_enable(id2, 1)
        pos1 = servo_radians_to_degrees(id1, rad1, block.servo_1.middle_pos)
        pos2 = servo_radians_to_degrees(id2, rad2, block.servo_2.middle_pos)
        base, side = decompose_finger(
            round(pos1),
            round(pos2),
            side_min=lim.side_min,
            side_max=lim.side_max,
            base_min=lim.base_min,
            base_max=lim.base_max,
        )
        fingers[name] = (int(base), int(side))
    return HandJogState(fingers=fingers)


def drive_finger(c, finger_name, block, base, side, speed):
    """Command one finger's two servos to (base, side), clamped to its limits."""
    lim = block.limits
    id1, id2 = FINGER_SERVO_IDS[finger_name]
    pos1, pos2 = compose_finger(
        base,
        side,
        base_min=lim.base_min,
        base_max=lim.base_max,
        side_min=lim.side_min,
        side_max=lim.side_max,
    )
    c.write_goal_speed(id1, speed)
    c.write_goal_speed(id2, speed)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, block.servo_1.middle_pos))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, block.servo_2.middle_pos))


def snapshot_pose(cfg, state):
    """Whole-hand (base, side) cursor -> per-finger HandPose (servo-frame degrees)."""
    # Cursor == live hardware position (torque is always ON), so no present-pos read needed.
    finger_data: dict[str, list[int]] = {}
    for name in FINGERS:
        id1, id2 = FINGER_SERVO_IDS[name]
        base, side = state.fingers[name]
        odd_val, even_val = finger_positions_to_servo_frame(id1, id2, base, side)
        finger_data[name] = [odd_val, even_val]
    return HandPose(**finger_data)


def maybe_save(cfg, hcfg, state):
    name = input("Save pose as (name, blank = cancel): ").strip()
    if not name:
        print("  (not saved)")
        return
    ok, err = validate_pose_name(name)
    if not ok:
        print(f"  invalid name: {err}")
        return
    hcfg.poses[name] = snapshot_pose(cfg, state)
    save_hand_config(HAND_CONFIG_PATH, hcfg)
    print(f"  saved pose '{name}' -> {HAND_CONFIG_PATH}")


def main():
    cfg = load_hand_calibration(YAML_PATH)
    hcfg: HandConfig = load_hand_config(HAND_CONFIG_PATH) if HAND_CONFIG_PATH.is_file() else HandConfig()
    limits_by_finger = cfg.limits_by_finger()
    all_ids = [sid for name in FINGERS for sid in FINGER_SERVO_IDS[name]]

    c = Scs0009PyController(
        serial_port=hcfg.connection.port,
        baudrate=hcfg.connection.baudrate,
        timeout=hcfg.connection.timeout,
    )
    print(__doc__)
    try:
        # Start from the hand's current pose (no-jump hold), not a forced neutral.
        state = hold_present_and_read_state(c, cfg)
        print("  " + format_hand_status(state))
        while True:
            action = key_to_action(read_key())
            if action is None:
                continue
            if action == "quit":
                break
            if action == "save":
                maybe_save(cfg, hcfg, state)
                continue

            state = apply_action(state, action, limits_by_finger)

            if action == "home_all":
                for name in FINGERS:
                    base, side = state.fingers[name]
                    drive_finger(c, name, cfg.fingers[name], base, side, hcfg.tuning.speed)
            else:
                base, side = state.fingers[state.active]
                drive_finger(c, state.active, cfg.fingers[state.active], base, side, hcfg.tuning.speed)

            id1, id2 = FINGER_SERVO_IDS[state.active]
            load1, load2 = read_loads(c, id1, id2)
            print("  " + format_hand_status(state))
            warn = load_warning(load1, load2)
            if warn:
                print("  " + warn)
    except KeyboardInterrupt:
        print("\n^C -- exiting")
    finally:
        # Never auto-home on quit. Offer 'h' to home all fingers to neutral first, or Enter
        # to release in place (light SCS0009s relax harmlessly either way).
        try:
            choice = (
                input(
                    "\nType 'h' + Enter to home all fingers to neutral first, or just Enter to release in place: "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            choice = ""
        if choice == "h":
            # Guarded so a homing failure can't skip the torque-off below (IL-4).
            try:
                for name in FINGERS:
                    drive_finger(c, name, cfg.fingers[name], 0, 0, hcfg.tuning.speed)
                time.sleep(1.0)  # let the fingers reach neutral before torque-off
                print("Homed all fingers to neutral.")
            except BaseException as e:  # incl. Ctrl+C during the settle -- must still cut torque
                print(f"  (homing interrupted: {e!r}) -- releasing torque anyway")
        for sid in all_ids:
            try:
                c.write_torque_enable(sid, 0)
            except Exception as e:
                print(f"warning: failed to disable torque on {sid}: {e}")


if __name__ == "__main__":
    main()
