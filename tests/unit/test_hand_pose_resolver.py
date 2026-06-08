"""Pure tests for ``arm101_hand.hand.pose_resolver`` (no hardware)."""

from __future__ import annotations

import math

import pytest

from arm101_hand.config import (
    DofLimits,
    HandCalibration,
    HandConfig,
    HandPose,
)
from arm101_hand.config.calibration import FingerCalibration, ServoCalibration
from arm101_hand.hand.pose_resolver import (
    DEFAULT_POSE_MARGIN_DEG,
    available_pose_names,
    resolve_hand_pose_targets,
)

# Per-servo calibrated neutrals, distinct so a mis-indexed lookup would show up.
_MIDDLE = {1: 30.0, 2: -2.0, 3: 28.0, 4: -4.0, 5: 26.0, 6: -6.0, 7: 24.0, 8: -8.0}
_BASE_MIN = -30.0
_BASE_MAX = 100.0


def _finger(id1: int, id2: int) -> FingerCalibration:
    return FingerCalibration(
        servo_1=ServoCalibration(middle_pos=_MIDDLE[id1]),
        servo_2=ServoCalibration(middle_pos=_MIDDLE[id2]),
        limits=DofLimits(base_min=_BASE_MIN, base_max=_BASE_MAX, side_min=-40.0, side_max=40.0),
    )


def _calib() -> HandCalibration:
    return HandCalibration(
        schema_version=3,
        fingers={
            "index": _finger(1, 2),
            "middle": _finger(3, 4),
            "ring": _finger(5, 6),
            "thumb": _finger(7, 8),
        },
    )


def _poses(**named: list[int]) -> HandConfig:
    """Build a HandConfig whose poses contain the given per-finger pair dicts.

    Each value in ``named`` must be a list of 8 ints (flat, for back-compat with
    the old test data). They are split into per-finger [servo_1, servo_2] pairs.
    """
    poses = {}
    finger_order = ("index", "middle", "ring", "thumb")
    for name, arr in named.items():
        poses[name] = HandPose(**{finger_order[i]: [arr[i * 2], arr[i * 2 + 1]] for i in range(4)})
    return HandConfig(poses=poses)


def test_stored_pose_decodes_per_servo() -> None:
    """A stored per-finger HandPose maps to deg2rad(stored + middle_pos) per servo id."""
    flat = [70, 2, 59, -19, 64, -14, 59, -59]  # the real 'grab' array (flat for ref)
    targets = resolve_hand_pose_targets(_calib(), _poses(grab=flat), "grab")

    assert sorted(targets) == [1, 2, 3, 4, 5, 6, 7, 8]
    for servo_id in range(1, 9):
        expected = math.radians(flat[servo_id - 1] + _MIDDLE[servo_id])
        assert math.isclose(targets[servo_id], expected, abs_tol=1e-9), f"servo {servo_id}"


@pytest.mark.parametrize(
    "pose,expected_base",
    [
        ("open", _BASE_MIN + DEFAULT_POSE_MARGIN_DEG),
        ("close", _BASE_MAX - DEFAULT_POSE_MARGIN_DEG),
    ],
)
def test_builtin_open_close_apply_margin(pose: str, expected_base: float) -> None:
    """open/close drive every finger to base_min+margin / base_max-margin, neutral spread.

    With side=0 the logical pos for both servos equals ``base``; odd IDs pass through
    (deg2rad(base + mp)), even IDs invert (deg2rad(-base + mp)).
    """
    targets = resolve_hand_pose_targets(_calib(), _poses(), pose)
    for servo_id in range(1, 9):
        signed = expected_base if servo_id % 2 == 1 else -expected_base
        expected = math.radians(signed + _MIDDLE[servo_id])
        assert math.isclose(targets[servo_id], expected, abs_tol=1e-9), f"servo {servo_id}"


def test_margin_override() -> None:
    targets = resolve_hand_pose_targets(_calib(), _poses(), "open", margin_deg=0.0)
    # margin 0 -> base == base_min for odd servo 1: deg2rad(base_min + mp1)
    assert math.isclose(targets[1], math.radians(_BASE_MIN + _MIDDLE[1]), abs_tol=1e-9)


def test_builtin_wins_over_stored_same_name() -> None:
    """A stored pose named 'open' must not shadow the built-in open."""
    bogus = [99, 99, 99, 99, 99, 99, 99, 99]
    targets = resolve_hand_pose_targets(_calib(), _poses(open=bogus), "open")
    # If the stored array had won, servo 1 would be deg2rad(99 + mp1); built-in uses base_min+margin.
    assert math.isclose(
        targets[1], math.radians(_BASE_MIN + DEFAULT_POSE_MARGIN_DEG + _MIDDLE[1]), abs_tol=1e-9
    )


def test_unknown_pose_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        resolve_hand_pose_targets(_calib(), _poses(), "nope")


def test_available_pose_names_dedups_and_orders() -> None:
    names = available_pose_names(_poses(grab=[0] * 8, open=[0] * 8, wave=[0] * 8))
    # Built-ins first (deduped against a same-named stored pose), then the rest in insertion order.
    assert names == ["open", "close", "grab", "wave"]
