"""Verify the SO-ARM101 calibration by sweeping joints to their calibrated endpoints.

Uses RANGE_M100_100 normalization, so the calibrated range maps to +/-100 with built-in
clamping -- driving to an endpoint is just commanding +/-margin. For each selected joint
the script: sets a gentle Goal_Velocity, enables torque, moves to mid-range (0) first,
then sweeps 0 -> +margin -> -margin -> 0, watching Present_Load for stalls.

After connect() the script pushes the on-file calibration to the motors
(bus.write_calibration) so the normalized endpoints physically match the recorded range
regardless of the motors' current EEPROM offsets. This writes the MOTORS, never the JSON
(IL-5).

On every exit path -- normal completion, Ctrl+C, or an error after torque-on -- the script
never auto-homes. It prompts: type 'h' to drive back to home first, or just Enter to release
torque in place. After a normal sweep each joint is back at mid-range (0); on an early exit a
joint may be mid-sweep, so use 'h' (or park by hand) if needed.

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
    load_home_degrees,
    safe_enable_torque,
)

from arm101_hand.robots.calibration_summary import ARM_JOINTS


def _move(follower, joint: str, target: float, *, load_stall: int, settle_s: float) -> None:
    """Command one joint to a normalized target (-100..100) and dwell, watching load."""
    follower.send_action({f"{joint}.pos": target})
    time.sleep(settle_s)
    load = follower.bus.read("Present_Load", joint, normalize=False)
    flag = "  <-- HIGH LOAD" if abs(load) >= load_stall else ""
    print(f"  {joint}: target={target:+6.1f}  load={load:>5}{flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep SO-ARM101 joints to calibrated endpoints.")
    parser.add_argument("joint", help=f"one of {', '.join(ARM_JOINTS)}, or 'all'")
    parser.add_argument(
        "--margin",
        type=float,
        default=None,
        help="how close to the endpoint to sweep, 1..99 (default from arm_config.yaml tuning; "
        "values are clamped to <=99 so the hard stop is never commanded)",
    )
    args = parser.parse_args()

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
    home = load_home_degrees()

    # Resolve margin: CLI arg overrides config default.
    raw_margin = args.margin if args.margin is not None else cfg.tuning.sweep_margin_default
    margin = max(1.0, min(99.0, raw_margin))

    load_stall: int = cfg.tuning.load_stall
    settle_s: float = cfg.tuning.settle_sweep_s

    torque_on = False
    print(f"Connecting on {cfg.connection.port} ...")
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.connection.port}: {e}", file=sys.stderr)
            return 1
        # Align the motors' onboard frame with the on-file calibration (writes motors, not JSON).
        follower.bus.write_calibration(follower.calibration)
        # No-jump torque-on: seed goal<-present + cap Goal_Velocity (reg 46, not the Dynamixel
        # "Profile_Velocity") BEFORE enabling torque, so a cold-booted bus holds the present pose
        # instead of snapping to a stale goal at max speed. Centering below then moves gently.
        safe_enable_torque(follower, vel)
        torque_on = True

        print(f"Centering all joints (Goal_Velocity={vel}) ...")
        follower.send_action({f"{j}.pos": 0.0 for j in ARM_JOINTS})
        time.sleep(settle_s)

        for joint in joints:
            print(f"\nSweeping {joint} (margin +/-{margin:.0f}):")
            _move(follower, joint, +margin, load_stall=load_stall, settle_s=settle_s)
            _move(follower, joint, -margin, load_stall=load_stall, settle_s=settle_s)
            _move(follower, joint, 0.0, load_stall=load_stall, settle_s=settle_s)
        print("\nSweep complete.")
    except KeyboardInterrupt:
        print("\n^C -- stopping")
    finally:
        # Never auto-home on exit. confirm_and_release offers 'h' (drive home first) or
        # Enter (release in place), then always releases torque.
        if follower.is_connected:
            confirm_and_release(follower, torque_on, home, vel, tuning=cfg.tuning)
            follower.disconnect()
            print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
