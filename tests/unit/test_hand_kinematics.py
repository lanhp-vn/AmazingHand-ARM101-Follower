"""Pure-math tests for ``arm101_hand.hand.kinematics``."""

from __future__ import annotations

import math

import pytest

from arm101_hand.hand.kinematics import (
    MAX_NAME_LEN,
    clamp,
    compose_finger,
    decompose_finger,
    degrees_to_servo_radians,
    even_id_inversion,
    finger_positions_to_servo_frame,
    servo_radians_to_degrees,
    validate_pose_name,
)


# | value | lo  | hi  | expected | description                       |
@pytest.mark.parametrize(
    "value,lo,hi,expected,desc",
    [
        (5, 0, 10, 5, "value inside range passes through"),
        (-3, 0, 10, 0, "below low clamps to lo"),
        (15, 0, 10, 10, "above high clamps to hi"),
        (0, 0, 10, 0, "lo boundary is inclusive"),
        (10, 0, 10, 10, "hi boundary is inclusive"),
        (-5.5, -10.0, 10.0, -5.5, "float inside range passes through"),
    ],
)
def test_clamp(value: float, lo: float, hi: float, expected: float, desc: str) -> None:
    assert clamp(value, lo, hi) == expected, desc


# | servo_id | value | expected | description                |
@pytest.mark.parametrize(
    "servo_id,value,expected,desc",
    [
        (1, 90, 90, "odd ID passes through unchanged"),
        (3, -45, -45, "odd ID with negative passes through"),
        (2, 90, -90, "even ID negates value"),
        (8, -45, 45, "even ID with negative becomes positive"),
        (1, 0, 0, "zero is unchanged for odd"),
        (2, 0, 0, "zero is its own negation for even"),
    ],
)
def test_even_id_inversion(servo_id: int, value: float, expected: float, desc: str) -> None:
    assert even_id_inversion(servo_id, value) == expected, desc


# Round-trip property: degrees → radians → degrees should be identity (within
# floating-point tolerance). Covers both odd and even IDs, all middle_pos
# polarities, and a range of deg_rel values.
# | servo_id | deg_rel | middle_pos | description                         |
@pytest.mark.parametrize(
    "servo_id,deg_rel,middle_pos,desc",
    [
        (1, 90.0, 30.0, "odd ID, positive middle, close direction"),
        (1, -45.0, 30.0, "odd ID, positive middle, open direction"),
        (2, 90.0, -2.0, "even ID, negative middle, close direction"),
        (2, -45.0, -2.0, "even ID, negative middle, open direction"),
        (5, 0.0, 22.0, "odd ID at neutral position"),
        (8, 110.0, -12.0, "even ID at far close"),
        (4, -40.0, 20.0, "even ID at far open"),
    ],
)
def test_degrees_servo_radians_roundtrip(servo_id: int, deg_rel: float, middle_pos: float, desc: str) -> None:
    rads = degrees_to_servo_radians(servo_id, deg_rel, middle_pos)
    back = servo_radians_to_degrees(servo_id, rads, middle_pos)
    assert math.isclose(back, deg_rel, abs_tol=1e-9), f"{desc}: got {back}, expected {deg_rel}"


def test_degrees_to_servo_radians_matches_calibration_convention() -> None:
    """Cross-check against the AmazingHand_FullHand_Test convention.

    Spec §15.3: the existing calibration script writes ``mp + 90`` for servo 1
    "close" and ``mp - 90`` for servo 2 "close". With ``deg_rel = 90``:
        - servo 1 (odd, mp=30): physical target = 30 + 90 = 120°
        - servo 2 (even, mp=-2): physical target = -2 - 90 = -92°
    """
    # Servo 1: odd, no inversion, abs = mp + deg_rel = 30 + 90 = 120°
    rads_1 = degrees_to_servo_radians(1, 90.0, 30.0)
    assert math.isclose(rads_1, math.radians(120.0), abs_tol=1e-9), "servo 1 close should target mp + 90°"
    # Servo 2: even, inversion, abs = mp + (-deg_rel) = -2 + (-90) = -92°
    rads_2 = degrees_to_servo_radians(2, 90.0, -2.0)
    assert math.isclose(rads_2, math.radians(-92.0), abs_tol=1e-9), "servo 2 close should target mp - 90°"


# compose → decompose → compose round-trip
# | base | side | description                                  |
@pytest.mark.parametrize(
    "base,side,desc",
    [
        (0, 0, "neutral pair"),
        (50, 0, "centered close"),
        (50, 10, "close with small right side"),
        (50, -10, "close with small left side"),
        (110, 0, "fully closed"),
        (0, 30, "open with right side"),
    ],
)
def test_compose_decompose_roundtrip(base: int, side: int, desc: str) -> None:
    pos1, pos2 = compose_finger(base, side)
    base_back, side_back = decompose_finger(pos1, pos2)
    assert (base_back, side_back) == (base, side), (
        f"{desc}: compose({base},{side})={pos1, pos2} → decompose={base_back, side_back}"
    )


# | pos1 | pos2 | servo_min | servo_max | description                 |
@pytest.mark.parametrize(
    "base,side,servo_min,servo_max,expected_pos1,expected_pos2,desc",
    [
        (0, 100, -40, 110, -40, 100, "side at upper saturates pos1 (lower clamp)"),
        (110, 50, -40, 110, 60, 110, "high base + side saturates pos2 at servo_max"),
        (50, 0, -40, 110, 50, 50, "no side gives equal pair"),
    ],
)
def test_compose_finger_clamps(
    base: int,
    side: int,
    servo_min: int,
    servo_max: int,
    expected_pos1: int,
    expected_pos2: int,
    desc: str,
) -> None:
    pos1, pos2 = compose_finger(base, side, servo_min, servo_max)
    assert (pos1, pos2) == (expected_pos1, expected_pos2), desc


# Forbidden chars per spec §5.5 / hand_logic.validate_name (21 explicit + control chars).
# | name | valid | description                          |
@pytest.mark.parametrize(
    "name,valid,desc",
    [
        ("fist", True, "lowercase ascii is valid"),
        ("Open Hand", True, "spaces inside name are valid"),
        ("ok_pose_42", True, "underscores and digits are valid"),
        ("", False, "empty rejected"),
        ("   ", False, "whitespace-only rejected"),
        (" leading", False, "leading space rejected"),
        ("trailing ", False, "trailing space rejected"),
        ("a:b", False, "colon rejected (YAML key separator)"),
        ("a{b", False, "open brace rejected"),
        ("a}b", False, "close brace rejected"),
        ("a[b", False, "open bracket rejected"),
        ("a]b", False, "close bracket rejected"),
        ("a,b", False, "comma rejected"),
        ("a&b", False, "ampersand rejected"),
        ("a*b", False, "asterisk rejected"),
        ("a#b", False, "hash rejected"),
        ("a?b", False, "question mark rejected"),
        ("a|b", False, "pipe rejected"),
        ("a-b", False, "hyphen rejected (YAML list marker)"),
        ("a<b", False, "less-than rejected"),
        ("a>b", False, "greater-than rejected"),
        ("a=b", False, "equals rejected"),
        ("a!b", False, "exclamation rejected"),
        ("a%b", False, "percent rejected"),
        ("a@b", False, "at-sign rejected"),
        ("a`b", False, "backtick rejected"),
        ('a"b', False, "double-quote rejected"),
        ("a'b", False, "single-quote rejected"),
        ("a\x01b", False, "control character rejected"),
        ("x" * (MAX_NAME_LEN + 1), False, f"name longer than {MAX_NAME_LEN} rejected"),
        ("x" * MAX_NAME_LEN, True, f"name exactly {MAX_NAME_LEN} accepted"),
    ],
)
def test_validate_pose_name(name: str, valid: bool, desc: str) -> None:
    is_valid, msg = validate_pose_name(name)
    assert is_valid == valid, f"{desc}: validity={is_valid}, msg={msg!r}"
    if not valid:
        assert msg, f"{desc}: invalid name should produce an error message"


def test_spread_sign_preserved() -> None:
    # Sign guard: a positive `side` (spread one way) must survive
    # compose->decompose without flipping. Uses an in-envelope asymmetric pose
    # so the per-servo clamp ([-40, 110]) does not interfere. This is the
    # invariant the earlier sign bug violated.
    pos1, pos2 = compose_finger(20, 30)
    assert (pos1, pos2) == (-10, 50), "compose: pos1 = base - side, pos2 = base + side"
    base, side = decompose_finger(pos1, pos2)
    assert (base, side) == (20, 30), "decompose preserves +side (no sign flip)"

    pos1, pos2 = compose_finger(0, -30)
    assert (pos1, pos2) == (30, -30), "negative side spreads the other way"
    base, side = decompose_finger(pos1, pos2)
    assert (base, side) == (0, -30), "decompose preserves -side"


def test_base_clamped_when_limits_passed() -> None:
    # base outside [base_min, base_max] is clamped ONLY when limits are passed.
    # base_max=80 is below servo_max=110 so this isolates the base clamp from
    # the per-servo clamp.
    pos1, pos2 = compose_finger(200, 0, base_min=-30, base_max=80)
    assert (pos1, pos2) == (80, 80), "base clamped to base_max=80 (side=0 -> both servos)"
    pos1, pos2 = compose_finger(-200, 0, base_min=-30, base_max=80)
    assert (pos1, pos2) == (-30, -30), "base clamped to base_min=-30"


def test_side_clamped_when_limits_passed() -> None:
    # side beyond side_max is clamped ONLY when limits are passed.
    pos1, pos2 = compose_finger(0, 99, side_min=-40, side_max=40)
    assert (pos1, pos2) == (-40, 40), "side clamped to side_max=40 (pos1=base-side, pos2=base+side)"


def test_clamps_are_opt_in() -> None:
    # With NO limit kwargs, behavior is unchanged: side is not clamped here, only
    # the per-servo clamp applies. Guards backward compatibility with the GUI.
    pos1, pos2 = compose_finger(0, 100, -40, 110)
    assert (pos1, pos2) == (-40, 100), "without limits, side is not clamped (legacy behavior)"


def test_finger_positions_odd_passthrough_even_negated() -> None:
    # Pure flexion (base=30, side=0): pos1=pos2=30.
    # Odd id (1): passthrough -> 30. Even id (2): negated -> -30.
    odd_val, even_val = finger_positions_to_servo_frame(1, 2, 30, 0)
    assert odd_val == 30
    assert even_val == -30


def test_finger_positions_round_trip() -> None:
    base, side = 25, 10
    odd_val, even_val = finger_positions_to_servo_frame(3, 4, base, side)
    # Invert the even-ID pre-inversion to get back logical pos1/pos2, then decompose.
    pos1 = int(even_id_inversion(3, float(odd_val)))
    pos2 = int(even_id_inversion(4, float(even_val)))
    got_base, got_side = decompose_finger(pos1, pos2)
    assert (got_base, got_side) == (base, side)
