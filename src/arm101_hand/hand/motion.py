"""Hand motion helpers: command SCS0009 servos and confirm arrival by position-polling.

Mirrors the arm's ``drive_arm_joints`` position-wait so BOTH devices confirm a move
finished (within tolerance) before the caller proceeds, instead of a blind sleep. A
gripping close that stalls on an object simply times out and continues
(timeout-then-continue) -- these helpers never raise and never hang. All positions are
SCS0009 wire-radians (what ``write_goal_position`` accepts and ``read_present_position``
returns); convert a degree tolerance with ``math.radians`` at the call site.
"""

from __future__ import annotations

import sys
import time

from arm101_hand.hand.protocol import SERVO_SYNC_S


def _scalar(v: object) -> float:
    """Unwrap rustypot's single-element list reads (``read_<reg>`` returns a list)."""
    if isinstance(v, (list, tuple)):
        return float(v[0])
    return float(v)  # type: ignore[arg-type]


def write_hand_servos(
    controller,
    targets: dict[int, float],
    speed: int,
    *,
    enable_torque: bool = True,
) -> None:
    """Command every servo to its wire-radians goal at ``speed`` (no wait).

    When enabling torque, first seed each goal to its PRESENT position and cap goal speed,
    THEN enable torque, THEN drive to ``targets`` at ``speed`` -- each goal write spaced by
    ``SERVO_SYNC_S`` for clean bus arbitration. An empty ``targets`` is a no-op. The caller
    owns torque-off.

    No-jump rationale: on a freshly-powered SCS0009 the SRAM ``goal_speed`` defaults to 0,
    which means *maximum* speed in position mode (not "stop"), and ``goal_position`` is stale,
    so enabling torque *before* setting them snaps the servo to a stale goal at full speed --
    the violent first-run-after-power-up move (SRAM resets every power-cycle, so only the first
    run(s) after plugging in are affected). Seeding goal<-present + a speed cap before torque-on
    makes torque-on hold the current pose gently. Mirrors the arm's ``safe_enable_torque``.
    """
    if enable_torque:
        # Seed goal<-present and cap speed BEFORE torque-on, so enabling torque holds the
        # current pose gently instead of snapping to a stale goal at max speed.
        for sid in sorted(targets):
            controller.write_goal_speed(sid, speed)
            controller.write_goal_position(sid, _scalar(controller.read_present_position(sid)))
            time.sleep(SERVO_SYNC_S)
        for sid in sorted(targets):
            controller.write_torque_enable(sid, 1)
    for sid in sorted(targets):
        controller.write_goal_speed(sid, speed)
        time.sleep(SERVO_SYNC_S)
    for sid in sorted(targets):
        controller.write_goal_position(sid, targets[sid])
        time.sleep(SERVO_SYNC_S)


def wait_hand_reached(
    controller,
    targets: dict[int, float],
    *,
    tolerance_rad: float,
    timeout_s: float,
    poll_s: float,
) -> bool:
    """Poll ``read_present_position`` until every servo in ``targets`` (id -> goal radians) is
    within ``tolerance_rad`` of its goal. Returns True if all reached; on timeout prints a note
    to stderr and returns False (never raises, never hangs). Empty ``targets`` -> True.
    """
    if not targets:
        return True
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        if all(
            abs(_scalar(controller.read_present_position(sid)) - goal) <= tolerance_rad
            for sid, goal in targets.items()
        ):
            return True
        time.sleep(poll_s)
    print("  (hand not fully reached within timeout -- continuing)", file=sys.stderr)
    return False


def drive_hand_servos(
    controller,
    targets: dict[int, float],
    speed: int,
    *,
    tolerance_rad: float,
    timeout_s: float,
    poll_s: float,
    enable_torque: bool = True,
) -> bool:
    """``write_hand_servos`` then ``wait_hand_reached``; returns the wait's bool."""
    write_hand_servos(controller, targets, speed, enable_torque=enable_torque)
    return wait_hand_reached(
        controller, targets, tolerance_rad=tolerance_rad, timeout_s=timeout_s, poll_s=poll_s
    )
