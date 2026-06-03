"""Read-only health check for the SO-ARM101 5-motor bus.

Pings motors 1-5 and, for each responder, reads present position (raw steps),
load, voltage, and temperature. Torque stays OFF the entire time. Uses a raw
FeetechMotorsBus (not the robot wrapper) so a missing/dead motor is reported
rather than crashing the robot's configure() step.

Exit code is non-zero if any of the 5 motors did not respond, so this doubles
as a pre-flight check before sweep.py / set_pose.py.

Usage:
  uv run python scripts/calibration/so_arm101/scan.py

IL-3: motors 1-5 only. IL-4: opens the bus briefly, releases it. Read-only.
"""

from __future__ import annotations

import sys

from _common import build_raw_bus, load_arm_app_config

from arm101_hand.robots.calibration_summary import ARM_JOINTS


def main() -> int:
    cfg = load_arm_app_config()
    bus = build_raw_bus(cfg)

    print(f"Opening arm bus on {cfg.arm.port} (read-only, torque off) ...")
    missing: list[str] = []
    try:
        # handshake=False: do not require every motor to answer at connect time --
        # we want to report which ones are missing.
        try:
            bus.connect(handshake=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1

        header = f"{'joint':<14}{'id':>3}{'model':>7}{'pos_raw':>9}{'load':>7}{'volt':>7}{'temp_c':>8}"
        print(header)
        print("-" * len(header))
        for joint in ARM_JOINTS:
            motor_id = bus.motors[joint].id
            model = bus.ping(joint, num_retry=2)
            if model is None:
                missing.append(joint)
                print(f"{joint:<14}{motor_id:>3}{'--':>7}{'(no response)':>34}")
                continue
            pos = bus.read("Present_Position", joint, normalize=False)
            load = bus.read("Present_Load", joint, normalize=False)
            volt = bus.read("Present_Voltage", joint, normalize=False)  # 0.1 V units (122 = 12.2 V)
            temp = bus.read("Present_Temperature", joint, normalize=False)
            print(f"{joint:<14}{motor_id:>3}{model:>7}{pos:>9}{load:>7}{volt / 10:>6.1f}V{temp:>7}")
            if temp >= cfg.safety.temp_warn_c:
                print(f"  WARNING: {joint} temp {temp}C >= warn threshold {cfg.safety.temp_warn_c}C")
    finally:
        # disable_torque=False: we never enabled torque; don't touch it on the way out.
        if bus.is_connected:
            bus.disconnect(disable_torque=False)
            print(f"Bus closed on {cfg.arm.port}.")

    if missing:
        print(f"\nMISSING motors: {missing}", file=sys.stderr)
        return 1
    print("\nAll 5 motors responded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
