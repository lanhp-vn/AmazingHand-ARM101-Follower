"""
AmazingHand Full-Hand Test Script

This script runs a complete sequence of movements for the 4-finger AmazingHand
(right hand configuration). It cycles through:
1. Closing all fingers into a fist
2. Opening all fingers
3. Isolating and moving the Index finger
4. Isolating and moving the Middle finger
5. Isolating and moving the Ring finger
6. Isolating and moving the Thumb
7. Returning to the neutral (initial) pose

It reads calibration values from 'hand_calib_values.yaml' and is intended
to be the final end-to-end sanity check that proves the calibration is correct.
"""

import contextlib
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_hand_calibration, load_hand_config
from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand import compose_finger, degrees_to_servo_radians
from arm101_hand.hand.protocol import SERVO_SYNC_S

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "hand_calib_values.yaml"
HAND_CONFIG_PATH = SCRIPT_DIR.parents[2] / "src" / "arm101_hand" / "data" / "hand_config.yaml"

_cfg = load_hand_calibration(YAML_PATH)
_hcfg = load_hand_config(HAND_CONFIG_PATH)
_FINGERS = _cfg.fingers

# Demo-specific speeds from hand_config.yaml tuning.speeds.
MaxSpeed = _hcfg.tuning.speeds.open  # extension (quicker)
CloseSpeed = _hcfg.tuning.speeds.close  # flexion (gentler settle)

c = Scs0009PyController(
    serial_port=_hcfg.connection.port,
    baudrate=_hcfg.connection.baudrate,
    timeout=_hcfg.connection.timeout,
)


# ---------- per-finger motion helpers ----------


def _move(name, base, side, speed):
    block = _FINGERS[name]
    id1, id2 = FINGER_SERVO_IDS[name]
    mp1 = block.servo_1.middle_pos
    mp2 = block.servo_2.middle_pos
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    time.sleep(SERVO_SYNC_S)
    c.write_goal_speed(id2, speed)
    time.sleep(SERVO_SYNC_S)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))
    time.sleep(0.005)


def _limits(name):
    return _FINGERS[name].limits


def Move_Index(base, side, speed):  # noqa: N802
    _move("index", base, side, speed)


def Move_Middle(base, side, speed):  # noqa: N802
    _move("middle", base, side, speed)


def Move_Ring(base, side, speed):  # noqa: N802
    _move("ring", base, side, speed)


def Move_Thumb(base, side, speed):  # noqa: N802
    _move("thumb", base, side, speed)


_MOVERS = {
    "index": Move_Index,
    "middle": Move_Middle,
    "ring": Move_Ring,
    "thumb": Move_Thumb,
}


# ---------- poses (right hand) ----------


def _close(name):
    return _limits(name).base_max, 0


def _open(name):
    return _limits(name).base_min, 0


def _spread_pose(name, frac):
    # An isolation pose: nearly open, spread by ``frac`` of the side range.
    lim = _limits(name)
    side = int((lim.side_max if frac > 0 else lim.side_min) * abs(frac))
    return lim.base_min, side


def InitialPose():  # noqa: N802
    for name, mover in _MOVERS.items():
        mover(*_open(name), MaxSpeed)


def OpenHand():  # noqa: N802
    for name, mover in _MOVERS.items():
        mover(*_open(name), MaxSpeed)


def CloseHand():  # noqa: N802
    for name, mover in _MOVERS.items():
        mover(*_close(name), CloseSpeed)


def _isolate(active):
    # Close every finger except ``active``, which sweeps its spread range.
    for name, mover in _MOVERS.items():
        if name != active:
            mover(*_close(name), MaxSpeed)
    mover = _MOVERS[active]
    mover(*_open(active), MaxSpeed)
    time.sleep(0.6)
    mover(*_spread_pose(active, 1.0), MaxSpeed)
    time.sleep(0.4)
    mover(*_spread_pose(active, -1.0), MaxSpeed)
    time.sleep(0.4)
    mover(*_open(active), MaxSpeed)


def IndexOnly():  # noqa: N802
    _isolate("index")


def MiddleOnly():  # noqa: N802
    _isolate("middle")


def RingOnly():  # noqa: N802
    _isolate("ring")


def ThumbOnly():  # noqa: N802
    _isolate("thumb")


def main():
    for finger_name in _FINGERS:
        id1, id2 = FINGER_SERVO_IDS[finger_name]
        c.write_torque_enable(id1, 1)
        c.write_torque_enable(id2, 1)

    print("Running AmazingHand full-hand demo (right hand, one cycle)...")
    try:
        CloseHand()
        time.sleep(2)

        OpenHand()
        time.sleep(1)

        IndexOnly()
        time.sleep(1.5)

        MiddleOnly()
        time.sleep(1.5)

        RingOnly()
        time.sleep(1.5)

        ThumbOnly()
        time.sleep(1.5)

        InitialPose()
        time.sleep(1)
        print("Cycle complete -- holding middle (initial) pose under torque.")
        input("Press Enter to release torque and exit... ")
    except KeyboardInterrupt:
        print("\n^C -- aborting")
    finally:
        for finger_name in _FINGERS:
            id1, id2 = FINGER_SERVO_IDS[finger_name]
            for sid in (id1, id2):
                with contextlib.suppress(Exception):
                    c.write_torque_enable(sid, 0)


if __name__ == "__main__":
    main()
