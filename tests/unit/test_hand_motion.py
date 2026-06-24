"""Unit tests for hand motion helpers (fake controller, no bus)."""

from __future__ import annotations

import math

from arm101_hand.hand import drive_hand_servos, wait_hand_reached, write_hand_servos


class _FakeController:
    def __init__(self, *, stuck: bool = False):
        self.present: dict[int, float] = {}
        self.torque: dict[int, int] = {}
        self.speed: dict[int, int] = {}
        self.calls: list[tuple] = []
        self._stuck = stuck

    def write_torque_enable(self, sid, on):
        self.torque[sid] = on
        self.calls.append(("torque", sid, on))

    def write_goal_speed(self, sid, sp):
        self.speed[sid] = sp
        self.calls.append(("speed", sid, sp))

    def write_goal_position(self, sid, rad):
        self.calls.append(("goal", sid, rad))
        if not self._stuck:
            self.present[sid] = rad  # instant arrival unless stuck (gripping)

    def read_present_position(self, sid):
        return [self.present.get(sid, 0.0)]  # rustypot returns a single-element list


def test_write_hand_servos_writes_torque_speed_goal():
    c = _FakeController()
    write_hand_servos(c, {1: 0.5, 2: -0.5}, speed=3)
    assert c.torque == {1: 1, 2: 1}
    assert c.speed == {1: 3, 2: 3}
    goals = {sid: rad for kind, sid, rad in c.calls if kind == "goal"}
    assert goals == {1: 0.5, 2: -0.5}


def test_write_hand_servos_seeds_present_and_speed_before_torque():
    c = _FakeController()
    c.present = {1: 0.9}  # stale present the servo must HOLD when torque turns on, not snap away
    write_hand_servos(c, {1: 0.1}, speed=3)

    torque_idx = next(i for i, call in enumerate(c.calls) if call[0] == "torque")
    pre = c.calls[:torque_idx]
    # Before torque-on: goal seeded to present AND speed capped, so enabling torque holds
    # the current pose gently instead of snapping to a stale goal at max speed (power-up lunge).
    assert ("goal", 1, 0.9) in pre, "goal seeded to present before torque-on"
    assert any(call[0] == "speed" and call[1] == 1 for call in pre), "speed capped before torque-on"
    # The real target is still commanded after torque is on.
    assert ("goal", 1, 0.1) in c.calls[torque_idx:]


def test_write_hand_servos_can_skip_torque():
    c = _FakeController()
    write_hand_servos(c, {1: 0.1}, speed=3, enable_torque=False)
    assert c.torque == {}
    assert any(kind == "goal" for kind, *_ in c.calls)


def test_wait_hand_reached_true_when_within_tolerance():
    c = _FakeController()
    c.present = {1: 0.50, 2: -0.50}
    assert (
        wait_hand_reached(c, {1: 0.51, 2: -0.49}, tolerance_rad=math.radians(5), timeout_s=1.0, poll_s=0.0)
        is True
    )


def test_wait_hand_reached_empty_is_true_without_reads():
    c = _FakeController()
    assert wait_hand_reached(c, {}, tolerance_rad=0.1, timeout_s=1.0, poll_s=0.0) is True
    assert c.calls == []


def test_wait_hand_reached_times_out_returns_false(capsys):
    c = _FakeController(stuck=True)
    c.present = {1: 0.0}
    ok = wait_hand_reached(c, {1: 1.0}, tolerance_rad=math.radians(5), timeout_s=0.05, poll_s=0.0)
    assert ok is False
    assert "hand not fully reached within timeout" in capsys.readouterr().err


def test_drive_hand_servos_writes_then_confirms():
    c = _FakeController()  # not stuck -> goal write sets present -> reaches immediately
    ok = drive_hand_servos(
        c, {1: 0.2, 2: 0.3}, speed=3, tolerance_rad=math.radians(5), timeout_s=1.0, poll_s=0.0
    )
    assert ok is True
    assert c.torque == {1: 1, 2: 1}


def test_drive_hand_servos_timeout_then_continue_returns_false(capsys):
    c = _FakeController(stuck=True)
    c.present = {1: 0.0}
    ok = drive_hand_servos(c, {1: 1.0}, speed=3, tolerance_rad=math.radians(5), timeout_s=0.05, poll_s=0.0)
    assert ok is False  # stalled (gripping) -> continues, never hangs
    assert "hand not fully reached within timeout" in capsys.readouterr().err


def test_wait_hand_reached_requires_all_servos():
    c = _FakeController()
    c.present = {1: 0.50, 2: 0.0}  # servo 2 nowhere near its goal
    ok = wait_hand_reached(c, {1: 0.50, 2: 1.0}, tolerance_rad=math.radians(5), timeout_s=0.05, poll_s=0.0)
    assert ok is False  # servo 1 reached but servo 2 did not -> must time out


def test_wait_hand_reached_returns_true_only_when_last_servo_arrives():
    c = _FakeController()
    c.present = {1: 0.50, 2: 0.50}  # both within tolerance of their goals
    ok = wait_hand_reached(c, {1: 0.50, 2: 0.50}, tolerance_rad=math.radians(5), timeout_s=1.0, poll_s=0.0)
    assert ok is True
