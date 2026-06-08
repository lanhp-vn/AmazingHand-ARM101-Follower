"""Read-only health check for the arm (5x STS3215) or hand (8x SCS0009) bus.

Pings every motor on the selected device and, for each responder, reads present
position, load, voltage, and temperature. Torque stays OFF the entire time.
Exit code is non-zero if any expected motor did not respond.

Usage:
  uv run python scripts/diagnostics/scan.py --device arm
  uv run python scripts/diagnostics/scan.py --device hand

IL-1: 12 V arm rail / 5 V hand rail (separate nominal voltages below).
IL-3: arm motors 1-5 (no gripper ID 6); hand servos 1-8.
IL-4: opens the bus briefly, releases it. Read-only.
"""

from __future__ import annotations

import argparse
import math
import sys

from arm101_hand.robots.calibration_summary import ARM_JOINTS
from arm101_hand.scripts.device_setup import build_raw_bus, load_arm_app_config

_ARM_NOMINAL_V = 12.0  # IL-1: 12 V arm rail.
_HAND_NOMINAL_V = 5.0  # IL-1: 5 V hand rail.
_HAND_SERVO_IDS = (1, 2, 3, 4, 5, 6, 7, 8)
# scripts/calibration/amazing_hand/hand_calib_values.yaml (canonical hand calib, IL-5).


def _scalar(value: object) -> float:
    return float(value[0]) if isinstance(value, list | tuple) else float(value)


def _scan_arm() -> int:
    cfg = load_arm_app_config()
    bus = build_raw_bus(cfg)
    print(f"Opening arm bus on {cfg.arm.port} (read-only, torque off) ...")
    missing: list[str] = []
    try:
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
            volt = bus.read("Present_Voltage", joint, normalize=False)
            temp = bus.read("Present_Temperature", joint, normalize=False)
            print(f"{joint:<14}{motor_id:>3}{model:>7}{pos:>9}{load:>7}{volt / 10:>6.1f}V{temp:>7}")
            _warn_arm(cfg, joint, temp, volt / 10)
    finally:
        if bus.is_connected:
            bus.disconnect(disable_torque=False)
            print(f"Bus closed on {cfg.arm.port}.")
    return _report(missing, len(ARM_JOINTS))


def _scan_hand() -> int:
    from pathlib import Path

    from rustypot import Scs0009PyController

    from arm101_hand.config import load_hand_config

    # scripts/diagnostics/scan.py -> repo root is parents[2].
    _repo_root = Path(__file__).resolve().parents[2]
    hand_config_path = _repo_root / "src" / "arm101_hand" / "data" / "hand_config.yaml"

    if not hand_config_path.is_file():
        print(f"hand config not found at {hand_config_path}", file=sys.stderr)
        return 1
    hcfg = load_hand_config(hand_config_path)
    print(f"Opening hand bus on {hcfg.connection.port} (read-only) ...")
    try:
        c = Scs0009PyController(
            serial_port=hcfg.connection.port,
            baudrate=hcfg.connection.baudrate,
            timeout=hcfg.connection.timeout,
        )
    except (ConnectionError, OSError) as e:
        print(f"ERROR: could not open {hcfg.connection.port}: {e}", file=sys.stderr)
        return 1
    missing: list[str] = []
    header = f"{'servo':>5}{'pos_deg':>9}{'load':>7}{'volt':>7}{'temp_c':>8}"
    print(header)
    print("-" * len(header))
    for sid in _HAND_SERVO_IDS:
        try:
            pos = _scalar(c.read_present_position(sid))
            load = _scalar(c.read_present_load(sid))
            volt = _scalar(c.read_present_voltage(sid)) * 0.1
            temp = _scalar(c.read_present_temperature(sid))
        except Exception:  # noqa: BLE001 -- a silent servo raises; report it missing.
            missing.append(str(sid))
            print(f"{sid:>5}{'(no response)':>31}")
            continue
        print(f"{sid:>5}{math.degrees(pos):>9.1f}{abs(int(load)):>7}{volt:>6.1f}V{temp:>7.0f}")
        _warn_hand(hcfg, f"servo {sid}", temp, volt)
    return _report(missing, len(_HAND_SERVO_IDS))


def _warn_arm(cfg, label: str, temp: float, volt_v: float) -> None:
    if temp >= cfg.safety.temp_warn_c:
        print(f"  WARNING: {label} temp {temp:.0f}C >= warn threshold {cfg.safety.temp_warn_c}C")
    dev_pct = abs(volt_v - _ARM_NOMINAL_V) / _ARM_NOMINAL_V * 100
    if dev_pct >= cfg.safety.voltage_warn_pct:
        print(
            f"  WARNING: {label} voltage {volt_v:.1f}V deviates {dev_pct:.1f}% "
            f"from {_ARM_NOMINAL_V:.0f}V nominal (warn {cfg.safety.voltage_warn_pct}%)"
        )


def _warn_hand(hcfg, label: str, temp: float, volt_v: float) -> None:
    if temp >= hcfg.safety.temp_warn_c:
        print(f"  WARNING: {label} temp {temp:.0f}C >= warn threshold {hcfg.safety.temp_warn_c}C")
    dev_pct = abs(volt_v - _HAND_NOMINAL_V) / _HAND_NOMINAL_V * 100
    if dev_pct >= hcfg.safety.voltage_warn_pct:
        print(
            f"  WARNING: {label} voltage {volt_v:.1f}V deviates {dev_pct:.1f}% "
            f"from {_HAND_NOMINAL_V:.0f}V nominal (warn {hcfg.safety.voltage_warn_pct}%)"
        )


def _report(missing: list[str], total: int) -> int:
    if missing:
        print(f"\nMISSING: {missing}", file=sys.stderr)
        return 1
    print(f"\nAll {total} motors responded.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only bus health check for arm or hand.")
    parser.add_argument("--device", required=True, choices=["arm", "hand"])
    args = parser.parse_args()
    return _scan_arm() if args.device == "arm" else _scan_hand()


if __name__ == "__main__":
    sys.exit(main())
