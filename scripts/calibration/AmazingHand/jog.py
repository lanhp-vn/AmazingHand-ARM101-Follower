"""AmazingHand Jog & Save-Pose tool -- arrow-key jog of all fingers, then save.

Move all four fingers with the keyboard (torque ON the whole time), then save the
resulting whole-hand pose by name into ``data/hand_config.yaml`` -- the same store the
unified GUI's pose manager uses. Mirrors the arm's ``so_arm101/jog.py``.

Config (serial + per-finger middle_pos + limits) is read from the canonical
``AmazingHand_calib_values.yaml`` (IL-5: this script never writes it).

Controls (torque ON the whole time):
  1 2 3 4       select active finger (index / middle / ring / thumb)
  Up / Down     active finger base + / -   (flex / extend)
  Right / Left  active finger side + / -   (spread)
  [ / ]         shrink / grow the jog step
  h             home the active finger to (0, 0) neutral
  H             home ALL fingers to (0, 0) neutral
  s             save the current whole-hand pose (prompts for a name)
  q / Ctrl+C    release torque on all 8 servos and exit (prints a reminder + asks to confirm)

On startup the cursor is initialized from the hand's CURRENT position (no auto-home) and
torque holds that pose without a jump; use ``h``/``H`` to home explicitly. Each finger's
cursor is clamped to its calibrated base/side limits, so you can only build poses inside
the known-good envelope. Windows-only: uses ``msvcrt`` for raw keys.
"""

import msvcrt
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import (
    HandPose,
    HandPoseConfig,
    load_hand_calibration,
    load_hand_poses,
    save_hand_poses,
)
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
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"
# scripts/calibration/AmazingHand/jog.py -> repo root is parents[2].
REPO_ROOT = SCRIPT_DIR.parents[2]
HAND_CONFIG_PATH = REPO_ROOT / "data" / "hand_config.yaml"


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
        s1, s2 = block.servo_1, block.servo_2
        rad1 = float(_scalar(c.read_present_position(s1.id)))
        rad2 = float(_scalar(c.read_present_position(s2.id)))
        # No-jump hold: command present, then enable torque.
        c.write_goal_position(s1.id, rad1)
        c.write_goal_position(s2.id, rad2)
        c.write_torque_enable(s1.id, 1)
        c.write_torque_enable(s2.id, 1)
        pos1 = servo_radians_to_degrees(s1.id, rad1, s1.middle_pos)
        pos2 = servo_radians_to_degrees(s2.id, rad2, s2.middle_pos)
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


def drive_finger(c, block, base, side, speed):
    """Command one finger's two servos to (base, side), clamped to its limits."""
    lim = block.limits
    pos1, pos2 = compose_finger(
        base,
        side,
        base_min=lim.base_min,
        base_max=lim.base_max,
        side_min=lim.side_min,
        side_max=lim.side_max,
    )
    c.write_goal_speed(block.servo_1.id, speed)
    c.write_goal_speed(block.servo_2.id, speed)
    c.write_goal_position(
        block.servo_1.id, degrees_to_servo_radians(block.servo_1.id, pos1, block.servo_1.middle_pos)
    )
    c.write_goal_position(
        block.servo_2.id, degrees_to_servo_radians(block.servo_2.id, pos2, block.servo_2.middle_pos)
    )


def snapshot_positions(cfg, state):
    """Whole-hand (base, side) cursor -> 8-int servo-frame positions array."""
    # Cursor == live hardware position (torque is always ON), so no present-pos read needed.
    out = [0] * 8
    for name in FINGERS:
        block = cfg.fingers[name]
        base, side = state.fingers[name]
        odd_val, even_val = finger_positions_to_servo_frame(block.servo_1.id, block.servo_2.id, base, side)
        out[block.servo_1.id - 1] = odd_val
        out[block.servo_2.id - 1] = even_val
    return out


def maybe_save(cfg, poses_cfg, state):
    name = input("Save pose as (name, blank = cancel): ").strip()
    if not name:
        print("  (not saved)")
        return
    ok, err = validate_pose_name(name)
    if not ok:
        print(f"  invalid name: {err}")
        return
    poses_cfg.poses[name] = HandPose(positions=snapshot_positions(cfg, state))
    save_hand_poses(HAND_CONFIG_PATH, poses_cfg)
    print(f"  saved pose '{name}' -> {HAND_CONFIG_PATH}")


def main():
    cfg = load_hand_calibration(YAML_PATH)
    poses_cfg = load_hand_poses(HAND_CONFIG_PATH) if HAND_CONFIG_PATH.is_file() else HandPoseConfig()
    limits_by_finger = cfg.limits_by_finger()
    all_ids = [s for block in cfg.fingers.values() for s in (block.servo_1.id, block.servo_2.id)]

    c = Scs0009PyController(
        serial_port=cfg.com_port,
        baudrate=cfg.baudrate,
        timeout=cfg.timeout,
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
                maybe_save(cfg, poses_cfg, state)
                continue

            state = apply_action(state, action, limits_by_finger)

            if action == "home_all":
                for name in FINGERS:
                    base, side = state.fingers[name]
                    drive_finger(c, cfg.fingers[name], base, side, cfg.speed)
            else:
                base, side = state.fingers[state.active]
                drive_finger(c, cfg.fingers[state.active], base, side, cfg.speed)

            block = cfg.fingers[state.active]
            load1, load2 = read_loads(c, block.servo_1.id, block.servo_2.id)
            print("  " + format_hand_status(state))
            warn = load_warning(load1, load2)
            if warn:
                print("  " + warn)
    except KeyboardInterrupt:
        print("\n^C -- exiting")
    finally:
        # No auto-home on quit. Remind + confirm before releasing torque (the fingers relax
        # in place; light SCS0009s don't drop, but kept consistent with the arm tools).
        print("\nReminder: releasing torque lets the fingers relax where they are (no auto-home).")
        try:
            input("Press Enter to release torque... ")
        except (EOFError, KeyboardInterrupt):
            print()
        for sid in all_ids:
            try:
                c.write_torque_enable(sid, 0)
            except Exception as e:
                print(f"warning: failed to disable torque on {sid}: {e}")


if __name__ == "__main__":
    main()
