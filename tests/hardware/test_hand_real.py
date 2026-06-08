"""Hardware smoke test for the AmazingHand bus (5 V SCS0009 x 8).

Skipped by default. Run with::

    uv run pytest -m hardware --port=COM18

Pre-flight (per IL-1 / IL-4):
- 5 V supply on the hand bus only -- the 12 V arm bus stays cold.
- No other process holds the port (close FD.exe, serial monitors, stale Python).

Drives ``rustypot.Scs0009PyController`` directly (same path as
``scripts/calibration/amazing_hand/jog.py``): connect, poll all 8 servos, nudge
the index finger's base by +5 deg, wait for arrival, return to neutral, then
torque-off. No GUI/Qt dependency.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from arm101_hand.config import load_hand_calibration, load_hand_config
from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand import compose_finger, degrees_to_servo_radians

CALIBRATION_PATH = (
    # Path after the Workstream 3 rename; this test is hardware-gated (skipped
    # without --port), so collection passes even before the rename lands.
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "calibration"
    / "amazing_hand"
    / "hand_calib_values.yaml"
)
HAND_CONFIG_PATH = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "hand_config.yaml"
ARRIVAL_TOLERANCE_DEG = 3.0
ARRIVAL_TIMEOUT_S = 4.0


def _scalar(value: object) -> float:
    return float(value[0]) if isinstance(value, list | tuple) else float(value)


@pytest.mark.hardware
def test_hand_connect_poll_nudge(port: str) -> None:
    from rustypot import Scs0009PyController

    cfg = load_hand_calibration(CALIBRATION_PATH)
    hcfg = load_hand_config(HAND_CONFIG_PATH)
    all_ids = [sid for finger_name in FINGER_SERVO_IDS for sid in FINGER_SERVO_IDS[finger_name]]
    c = Scs0009PyController(
        serial_port=port,
        baudrate=hcfg.connection.baudrate,
        timeout=hcfg.connection.timeout,
    )
    try:
        for sid in all_ids:
            c.write_torque_enable(sid, 1)
        # Poll all 8 -- every servo must answer.
        for sid in all_ids:
            assert c.read_present_position(sid) is not None, f"servo {sid} did not respond"

        block = cfg.fingers["index"]
        s1_id, s2_id = FINGER_SERVO_IDS["index"]
        lim = block.limits

        def drive(base: float) -> None:
            pos1, pos2 = compose_finger(
                base,
                0.0,
                base_min=lim.base_min,
                base_max=lim.base_max,
                side_min=lim.side_min,
                side_max=lim.side_max,
            )
            c.write_goal_speed(s1_id, hcfg.tuning.speed)
            c.write_goal_speed(s2_id, hcfg.tuning.speed)
            c.write_goal_position(s1_id, degrees_to_servo_radians(s1_id, pos1, block.servo_1.middle_pos))
            c.write_goal_position(s2_id, degrees_to_servo_radians(s2_id, pos2, block.servo_2.middle_pos))

        target = min(5.0, lim.base_max)
        drive(target)
        deadline = time.monotonic() + ARRIVAL_TIMEOUT_S
        while time.monotonic() < deadline:
            deg = float(np.rad2deg(_scalar(c.read_present_position(s1_id)))) - block.servo_1.middle_pos
            if abs(deg - target) <= ARRIVAL_TOLERANCE_DEG:
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"index s1 did not reach {target} deg within {ARRIVAL_TIMEOUT_S}s")
        drive(0.0)
        time.sleep(0.5)
    finally:
        for sid in all_ids:
            try:  # noqa: SIM105 -- best-effort torque-off on teardown; contextlib.suppress is less clear
                c.write_torque_enable(sid, 0)
            except Exception:  # noqa: BLE001
                pass
