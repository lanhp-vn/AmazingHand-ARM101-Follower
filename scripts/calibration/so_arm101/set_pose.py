"""Drive the SO-ARM101 to a named pose (degrees) and hold it under torque until Enter.

Poses come from src/arm101_hand/data/arm_config.yaml ``poses`` (home -- the folded storage
pose -- plus any poses saved via jog.py). Targets are in DEGREES relative to each joint's
calibrated mid. An out-of-range value is warned and that joint is skipped.

After connect() the script pushes the on-file calibration to the motors so degree targets
physically match the recorded range (writes the MOTORS, never the JSON -- IL-5).

On exit (Enter, Ctrl+C, or EOF) the script never auto-homes a held pose. For any pose except
'home' it prompts: type 'h' to drive back to home first, or just Enter to release torque in
place (the arm sags under gravity from a raised pose). When driving to 'home' there is nothing
to return to, so it simply settles at home and releases without prompting. Then torque is off.

Usage:
  uv run python scripts/calibration/so_arm101/set_pose.py home
  uv run python scripts/calibration/so_arm101/set_pose.py        # prompts

IL-3: motors 1-5. IL-4: single bus owner; torque released in finally.
"""

from __future__ import annotations

import sys
import time

from _common import (
    ARM_CONFIG_PATH,
    CALIB_PATH,
    build_follower,
    confirm_and_release,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
    safe_enable_torque,
)

from arm101_hand.config import load_arm_config
from arm101_hand.robots.calibration_summary import ARM_JOINTS, clamp_degrees, load_arm_calibration


def _resolve_pose_name(argv: list[str], available: list[str]) -> str:
    if len(argv) > 1 and argv[1] in available:
        return argv[1]
    if len(argv) > 1:
        print(f"  {argv[1]!r} is not a known pose; choose from {available}")
    while True:
        try:
            choice = input(f"Pose? ({'/'.join(available)}): ").strip()
        except EOFError:
            print("\nNo input -- exiting.", file=sys.stderr)
            raise SystemExit(1) from None
        if choice in available:
            return choice
        print(f"  invalid -- pick one of {available}")


def main() -> int:
    poses = load_arm_config(ARM_CONFIG_PATH).poses
    if not poses:
        print(f"no poses defined in {ARM_CONFIG_PATH} (poses)", file=sys.stderr)
        return 1
    available = sorted(poses)
    name = _resolve_pose_name(sys.argv, available)
    pose = poses[name].as_dict()  # {joint: degrees}

    calib = load_arm_calibration(CALIB_PATH)
    # Clamp each joint to its usable degree window; skip out-of-range joints.
    targets: dict[str, float] = {}
    for joint in ARM_JOINTS:
        raw = pose[joint]
        clamped = clamp_degrees(raw, calib[joint].range_min, calib[joint].range_max)
        if abs(clamped - raw) > 1e-6:
            print(f"  WARNING: {joint} target {raw:.1f} deg out of range -> skipped")
            continue
        targets[joint] = clamped

    if not targets:
        print("ERROR: all joints out of range -- nothing to drive", file=sys.stderr)
        return 1

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)  # DEGREES
    vel = gentle_velocity(cfg)
    home = load_home_degrees()

    settle_s: float = cfg.tuning.settle_set_pose_s

    torque_on = False
    print(f"Connecting on {cfg.connection.port}; driving to '{name}' ...")
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.connection.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        # No-jump torque-on: seed goal<-present + cap Goal_Velocity (reg 46) before enabling
        # torque, so a cold-booted bus holds the present pose instead of snapping to a stale
        # goal at max speed; send_action then drives to the target at the capped speed.
        safe_enable_torque(follower, vel)
        torque_on = True
        follower.send_action({f"{j}.pos": v for j, v in targets.items()})
        time.sleep(settle_s)
        print(f"Holding '{name}' under torque: {targets}")
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- exiting")
    finally:
        # Never auto-home a held pose. For any pose except 'home' itself, confirm_and_release
        # offers 'h' (drive home first) or Enter (release in place). When driving to 'home',
        # there is nothing to return to -- it settles at home and releases without prompting.
        if follower.is_connected:
            confirm_and_release(
                follower, torque_on, home, vel, offer_home=(name != "home"), tuning=cfg.tuning
            )
            follower.disconnect()
            print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
