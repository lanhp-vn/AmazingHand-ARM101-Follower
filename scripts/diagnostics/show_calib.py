"""Pretty-print the arm or hand calibration; optionally compare to live position.

Arm: reads ``so101_follower.json`` (id/homing/min/max/span/midpoint). Hand:
reads the AmazingHand calib YAML (per-finger servo middle_pos + DOF limits).
``--live`` additionally opens the selected bus read-only (torque stays off).

Usage:
  uv run python scripts/diagnostics/show_calib.py --device arm [--live]
  uv run python scripts/diagnostics/show_calib.py --device hand [--live]

IL-5: never writes any calibration file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _hand_paths import HAND_CALIB_PATH

from arm101_hand.robots.calibration_summary import (
    ARM_JOINTS,
    degree_span,
    load_arm_calibration,
    midpoint_steps,
)
from arm101_hand.scripts.device_setup import CALIB_PATH, build_follower, load_arm_app_config


def _scalar(value: object) -> float:
    return float(value[0]) if isinstance(value, list | tuple) else float(value)


def _show_arm(live: bool) -> int:
    if not CALIB_PATH.is_file():
        print(f"calibration not found at {CALIB_PATH}", file=sys.stderr)
        return 1
    calib = load_arm_calibration(CALIB_PATH)
    print(f"Calibration: {CALIB_PATH}")
    header = f"{'joint':<14}{'id':>3}{'homing':>9}{'min':>7}{'max':>7}{'span_deg':>10}{'mid_steps':>11}"
    print(header)
    print("-" * len(header))
    for joint in ARM_JOINTS:
        c = calib[joint]
        print(
            f"{joint:<14}{c.id:>3}{c.homing_offset:>9}{c.range_min:>7}{c.range_max:>7}"
            f"{degree_span(c.range_min, c.range_max):>10.2f}{midpoint_steps(c.range_min, c.range_max):>11.1f}"
        )
    if not live:
        return 0
    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)
    print(f"\nConnecting read-only on {cfg.arm.port} (torque stays off) ...")
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        obs = follower.get_observation()
        print(f"\n{'joint':<14}{'present_deg':>12}{'mid_deg':>10}")
        print("-" * 36)
        for joint in ARM_JOINTS:
            present = obs.get(f"{joint}.pos", float("nan"))
            print(f"{joint:<14}{present:>12.2f}{0.0:>10.2f}")
    finally:
        if follower.is_connected:
            follower.disconnect()
        print("Bus closed.")
    return 0


def _show_hand(live: bool) -> int:
    from arm101_hand.config import load_hand_calibration, load_hand_config
    from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
    from arm101_hand.hand import servo_radians_to_degrees

    # scripts/diagnostics/show_calib.py -> repo root is parents[2].
    _repo_root = Path(__file__).resolve().parents[2]
    hand_config_path = _repo_root / "src" / "arm101_hand" / "data" / "hand_config.yaml"

    if not HAND_CALIB_PATH.is_file():
        print(f"calibration not found at {HAND_CALIB_PATH}", file=sys.stderr)
        return 1
    cfg = load_hand_calibration(HAND_CALIB_PATH)
    print(f"Calibration: {HAND_CALIB_PATH}")
    header = (
        f"{'finger':<8}{'s1':>3}{'mid1':>8}{'s2':>4}{'mid2':>8}"
        f"{'base_min':>10}{'base_max':>10}{'side_min':>10}{'side_max':>10}"
    )
    print(header)
    print("-" * len(header))
    for name, block in cfg.fingers.items():
        id1, id2 = FINGER_SERVO_IDS[name]
        lim = block.limits
        print(
            f"{name:<8}{id1:>3}{block.servo_1.middle_pos:>8.1f}"
            f"{id2:>4}{block.servo_2.middle_pos:>8.1f}"
            f"{lim.base_min:>10.1f}{lim.base_max:>10.1f}{lim.side_min:>10.1f}{lim.side_max:>10.1f}"
        )
    if not live:
        return 0
    from rustypot import Scs0009PyController

    if not hand_config_path.is_file():
        print(f"hand config not found at {hand_config_path} (needed for --live)", file=sys.stderr)
        return 1
    hcfg = load_hand_config(hand_config_path)
    print(f"\nConnecting read-only on {hcfg.connection.port} (torque stays off) ...")
    try:
        c = Scs0009PyController(
            serial_port=hcfg.connection.port,
            baudrate=hcfg.connection.baudrate,
            timeout=hcfg.connection.timeout,
        )
    except (ConnectionError, OSError) as e:
        print(f"ERROR: could not open {hcfg.connection.port}: {e}", file=sys.stderr)
        return 1
    print(f"\n{'servo':>5}{'present_deg':>12}{'mid_pos':>9}")
    print("-" * 26)
    for name, block in cfg.fingers.items():
        id1, id2 = FINGER_SERVO_IDS[name]
        for servo_id, mid in ((id1, block.servo_1.middle_pos), (id2, block.servo_2.middle_pos)):
            rad = _scalar(c.read_present_position(servo_id))
            deg = servo_radians_to_degrees(servo_id, rad, mid)
            print(f"{servo_id:>5}{deg:>12.2f}{mid:>9.1f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Show arm or hand calibration.")
    parser.add_argument("--device", required=True, choices=["arm", "hand"])
    parser.add_argument("--live", action="store_true", help="also read present positions (read-only)")
    args = parser.parse_args()
    return _show_arm(args.live) if args.device == "arm" else _show_hand(args.live)


if __name__ == "__main__":
    sys.exit(main())
