"""Verify the SO-ARM101 calibration by sweeping joints to their calibrated endpoints.

Uses RANGE_M100_100 normalization, so the calibrated range maps to +/-100 with built-in
clamping -- driving to an endpoint is just commanding +/-margin. For each selected joint
the script: sets a gentle Goal_Velocity, enables torque, moves to mid-range (0) first,
then sweeps 0 -> +margin -> -margin -> 0, watching Present_Load for stalls.

After connect() the script pushes the on-file calibration to the motors
(bus.write_calibration) so the normalized endpoints physically match the recorded range
regardless of the motors' current EEPROM offsets. This writes the MOTORS, never the JSON
(IL-5).

On every exit path -- normal completion, Ctrl+C, or an error after torque-on -- torque is
released IN PLACE (no auto-home; that would be a surprise movement). After a normal sweep each
joint is back at mid-range (0); on an early exit a joint may be mid-sweep. The script prints a
reminder and waits for Enter before releasing, so you can park the arm by hand if needed.

Usage:
  uv run python scripts/calibration/so_arm101/sweep.py shoulder_pan
  uv run python scripts/calibration/so_arm101/sweep.py all --margin 80

IL-3: motors 1-5. IL-4: single bus owner; torque released in finally.
"""

from __future__ import annotations

import argparse
import sys
import time

from _common import (
    build_follower,
    confirm_and_release,
    gentle_velocity,
    load_arm_app_config,
)

from arm101_hand.robots.calibration_summary import ARM_JOINTS

# Present_Load magnitude above which we treat the joint as stalling against a stop.
_LOAD_STALL = 600
_SETTLE_S = 1.2  # dwell after each commanded target so the joint reaches it


def _move(follower, joint: str, target: float) -> None:
    """Command one joint to a normalized target (-100..100) and dwell, watching load."""
    follower.send_action({f"{joint}.pos": target})
    time.sleep(_SETTLE_S)
    load = follower.bus.read("Present_Load", joint, normalize=False)
    flag = "  <-- HIGH LOAD" if abs(load) >= _LOAD_STALL else ""
    print(f"  {joint}: target={target:+6.1f}  load={load:>5}{flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep SO-ARM101 joints to calibrated endpoints.")
    parser.add_argument("joint", help=f"one of {', '.join(ARM_JOINTS)}, or 'all'")
    parser.add_argument(
        "--margin",
        type=float,
        default=90.0,
        help="how close to the endpoint to sweep, 1..99 (default 90; values are clamped to <=99 so the hard stop is never commanded)",
    )
    args = parser.parse_args()

    margin = max(1.0, min(99.0, args.margin))
    if args.joint == "all":
        joints = list(ARM_JOINTS)
    elif args.joint in ARM_JOINTS:
        joints = [args.joint]
    else:
        print(f"unknown joint {args.joint!r}; pick one of {ARM_JOINTS} or 'all'", file=sys.stderr)
        return 1

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=False)  # RANGE_M100_100
    vel = gentle_velocity(cfg)

    torque_on = False
    print(f"Connecting on {cfg.arm.port} ...")
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        # Align the motors' onboard frame with the on-file calibration (writes motors, not JSON).
        follower.bus.write_calibration(follower.calibration)
        # Gentle speed BEFORE enabling torque / first move, so nothing snaps.
        # STS3215 movement-speed register is "Goal_Velocity" (reg 46), not the
        # Dynamixel-style "Profile_Velocity".
        follower.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, vel))
        follower.bus.enable_torque()
        torque_on = True

        print(f"Centering all joints (Goal_Velocity={vel}) ...")
        follower.send_action({f"{j}.pos": 0.0 for j in ARM_JOINTS})
        time.sleep(_SETTLE_S)

        for joint in joints:
            print(f"\nSweeping {joint} (margin +/-{margin:.0f}):")
            _move(follower, joint, +margin)
            _move(follower, joint, -margin)
            _move(follower, joint, 0.0)
        print("\nSweep complete.")
    except KeyboardInterrupt:
        print("\n^C -- stopping")
    finally:
        # No auto-home on exit (a surprise movement). Release torque in place; if still
        # holding, confirm_and_release prints a reminder + waits for Enter before releasing.
        if follower.is_connected:
            confirm_and_release(follower, torque_on)
            follower.disconnect()
            print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
