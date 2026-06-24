"""``arm101-arm-torque`` console-script: toggle torque on SO-ARM101 motors 1-5.

Interactive one-shot: prompt for ``e`` (enable) or ``d`` (disable), open the arm
bus, write ``Torque_Enable`` accordingly, release the port. Reads the arm port
from ``src/arm101_hand/data/arm_config.yaml``; motor identity comes from the
``SO101FollowerNoGripper`` subclass (single source -- IL-3).

IL-3: motor 6 (gripper) is omitted -- physically absent.
IL-4: opens the arm bus briefly and releases it; nothing else may hold the port.
"""

from __future__ import annotations

import sys

from pydantic import ValidationError

from arm101_hand.config import load_arm_config
from arm101_hand.scripts.device_setup import (
    ARM_CONFIG_PATH,
    build_follower,
    gentle_velocity,
    safe_enable_torque,
)


def main() -> int:
    try:
        cfg = load_arm_config(ARM_CONFIG_PATH)
    except FileNotFoundError:
        print(f"arm_config.yaml missing at {ARM_CONFIG_PATH}", file=sys.stderr)
        return 1
    except ValidationError as e:
        print(f"arm_config.yaml failed validation:\n{e}", file=sys.stderr)
        return 1

    follower = build_follower(cfg, use_degrees=True)
    vel = gentle_velocity(cfg)
    motor_ids = [m.id for m in follower.bus.motors.values()]

    print(f"Opening arm bus on {cfg.connection.port} ...")
    follower.bus.connect()
    try:
        while True:
            try:
                choice = input("Torque [e]nable / [d]isable (q to quit): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()  # newline after ^C / EOF
                break
            if choice == "e":
                # No-jump torque-on: seed goal<-present + cap velocity first, so enabling torque
                # holds the arm where it currently is rather than snapping to a stale goal at max
                # speed (the power-up lunge). Re-enabling after a hand-reposition holds the new pose.
                safe_enable_torque(follower, vel)
                print(f"Torque ENABLED on motors {motor_ids}.")
            elif choice == "d":
                follower.bus.disable_torque()
                print(f"Torque DISABLED on motors {motor_ids}.")
            elif choice == "q":
                break
            elif choice == "":
                # Empty Enter = nudge, not quit. Re-prompt.
                continue
            else:
                print(f"Unrecognized: {choice!r} — type e, d, or q.")
    finally:
        # Release the port without re-toggling torque (we just set the state
        # the user asked for -- don't let the bus undo it on disconnect).
        follower.bus.disconnect(disable_torque=False)
        print(f"Bus closed on {cfg.connection.port}. Torque state preserved.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
