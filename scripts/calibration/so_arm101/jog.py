"""Interactive keyboard jog for the SO-ARM101 follower (manual per-motor positioning).

Select a joint (1-5) and nudge it in degrees (clamped to its calibrated range). Toggle
torque to hand-pose the arm, home a single joint, or save the current pose to
data/arm_jog_poses.yaml (drivable later by set_pose.py).

Controls (torque ON to move):
  1..5          select joint (shoulder_pan shoulder_lift elbow_flex wrist_flex wrist_roll)
  Up / Down     jog active joint + / - step (deg), clamped to calibrated range
  [ / ]         shrink / grow jog step (1..15 deg)
  h             home active joint to 0 (calibrated mid)
  t             toggle torque (off = hand-pose by hand; on = resync + hold)
  s             save current pose to data/arm_jog_poses.yaml (prompts for a name)
  q / Ctrl+C    return all joints home, release torque, exit
                (if torque is OFF, just disconnects in place)

Windows-only: uses msvcrt.getwch for raw key reads. Pure jog logic is in
arm101_hand.robots.arm_jog (unit-tested); this file only does I/O.

IL-3: motors 1-5. IL-4: single bus owner; torque released on exit. IL-5: never writes
so101_follower.json (reads it for the clamp range).
"""

from __future__ import annotations

import msvcrt
import sys

from _common import (
    ARM_JOG_POSES_PATH,
    CALIB_PATH,
    build_follower,
    gentle_velocity,
    load_arm_app_config,
    park_home_and_release,
)

from arm101_hand.config import ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses
from arm101_hand.robots.arm_jog import (
    ARM_JOINTS,
    apply_action,
    format_status,
    initial_state,
    key_to_action,
)
from arm101_hand.robots.calibration_summary import degree_bounds, load_arm_calibration

_LOAD_WARN = 600


def read_key() -> str:
    """Block for one key; normalize arrows to UP/DOWN/LEFT/RIGHT tokens."""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix
        code = msvcrt.getwch()
        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(code, "")
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def _present_degrees(follower) -> dict[str, float]:
    obs = follower.get_observation()  # {f"{joint}.pos": degrees}
    return {j: float(obs[f"{j}.pos"]) for j in ARM_JOINTS}


def _hold_at(follower, cursors: dict[str, float], vel: int) -> None:
    """Set goal=cursors at gentle velocity, then enable torque (no-jump hold)."""
    follower.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, vel))
    follower.send_action({f"{j}.pos": cursors[j] for j in ARM_JOINTS})
    follower.bus.enable_torque()


def _save_pose(follower) -> None:
    """Prompt for a name and save current LIVE present pose to arm_jog_poses.yaml."""
    try:
        name = input("\n  save pose as (name, blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("  save cancelled")
        return
    if not name:
        print("  save cancelled")
        return
    config = load_arm_poses(ARM_JOG_POSES_PATH) if ARM_JOG_POSES_PATH.is_file() else ArmPoseConfig()
    config.poses[name] = ArmPose(**_present_degrees(follower))
    save_arm_poses(ARM_JOG_POSES_PATH, config)
    print(f"  saved '{name}' -> {ARM_JOG_POSES_PATH}")


def main() -> int:
    cfg = load_arm_app_config()
    if not CALIB_PATH.is_file():
        print(f"calibration not found at {CALIB_PATH}", file=sys.stderr)
        return 1
    calib = load_arm_calibration(CALIB_PATH)
    bounds = {j: degree_bounds(calib[j].range_min, calib[j].range_max) for j in ARM_JOINTS}

    follower = build_follower(cfg, use_degrees=True)
    vel = gentle_velocity(cfg)

    print(__doc__)
    print(f"Connecting on {cfg.arm.port} ...")
    state = None
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        cursors = _present_degrees(follower)
        _hold_at(follower, cursors, vel)
        state = initial_state(cursors)
        print(format_status(state, follower.bus.sync_read("Present_Load")))

        while True:
            action = key_to_action(read_key())
            state, effect = apply_action(state, action, bounds)
            if effect == "quit":
                break
            if effect == "toggle_torque":
                if state.torque_on:
                    state.cursors = _present_degrees(follower)
                    _hold_at(follower, state.cursors, vel)
                    print("  torque ON (holding current pose)")
                else:
                    follower.bus.disable_torque()
                    print("  torque OFF -- hand-pose the arm; press t to re-hold")
            elif effect == "save":
                _save_pose(follower)
            elif effect == "move":
                follower.send_action({f"{state.active}.pos": state.cursors[state.active]})
            elif action in ("jog_up", "jog_down", "home_active") and not state.torque_on:
                print("  (torque OFF -- press t to enable before jogging)")

            loads = follower.bus.sync_read("Present_Load") if state.torque_on else {}
            print(format_status(state, loads))
            high = [j for j, v in loads.items() if abs(v) >= _LOAD_WARN]
            if high:
                print(f"  WARNING: high load on {high}")
    except KeyboardInterrupt:
        print("\n^C -- exiting")
    finally:
        # Return home then release torque, unless torque was toggled off (hand-pose mode),
        # in which case leave the arm where it is and just disconnect.
        if follower.is_connected:
            if state is not None and state.torque_on:
                print("Returning to home before releasing torque ...")
                park_home_and_release(follower, vel)
                print("Home reached; torque off.")
            else:
                follower.bus.disable_torque()
            follower.disconnect()
            print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
