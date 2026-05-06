"""Pure-math helpers for the AmazingHand. No hardware contact, no I/O.

Two coordinate frames matter here:

- **Logical frame** (positive = close, same direction for both servos in a finger
  pair). The slider, keyboard handler, and pose-manager UI all work in this
  frame. Range conventions match AmazingHandControl: per-servo [-40°, +110°],
  per-finger base [0°, +110°], per-finger side [-40°, +40°].
- **Servo frame** (the motor's own reported angle). Even-ID servos in each
  finger pair are mounted in a mirrored orientation, so their physical "close"
  direction is the negative of their odd-ID partner's. ``even_id_inversion``
  is the conversion between logical and servo frames; it is its own inverse.

``degrees_to_servo_radians`` and ``servo_radians_to_degrees`` go all the way
from logical-frame degrees-relative-to-calibrated-middle to wire-format
radians (what rustypot's ``Scs0009PyController.write_goal_position`` accepts)
and back. The middle_pos comes from
``scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml``.

Adapted from ``references/AmazingHandControl/hand_logic.py`` (Apache-2.0,
Ingo Dering, 2026) per IL-2: the `validate_pose_name`, `clamp`, `angle_rad`,
`compute_auto_positions`, and `decompose_servo_positions` shapes all originate
there. Renamed and trimmed (no ``auto_extremes`` interpolation, no telemetry
formatters) for the unified-GUI scope; see ``docs/plans/01-unified-gui-spec.md``
§5.2.
"""

from __future__ import annotations

import numpy as np

# Forbidden chars rejected by ``validate_pose_name``. YAML control characters,
# whitespace-causing punctuation, and shell-injection-flavored symbols.
# Keeping the list explicit (not a regex) makes the error messages precise.
_FORBIDDEN_NAME_CHARS: tuple[str, ...] = (
    ":",
    "{",
    "}",
    "[",
    "]",
    ",",
    "&",
    "*",
    "#",
    "?",
    "|",
    "-",
    "<",
    ">",
    "=",
    "!",
    "%",
    "@",
    "`",
    '"',
    "'",
)

MAX_NAME_LEN = 50


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value into [lo, hi]. Pure; works for int and float."""
    return max(lo, min(hi, value))


def validate_pose_name(name: str) -> tuple[bool, str]:
    """Reject names that would corrupt YAML or surprise the user.

    Returns ``(is_valid, error_message)``. Empty error means valid.
    """
    if not name or not name.strip():
        return False, "Name cannot be empty"

    if name != name.strip():
        return False, "Name has leading/trailing spaces"

    if len(name) > MAX_NAME_LEN:
        return False, f"Name too long (max {MAX_NAME_LEN} characters)"

    for char in _FORBIDDEN_NAME_CHARS:
        if char in name:
            return False, f"Name contains forbidden character: {char}"

    if any(ord(c) < 32 for c in name):
        return False, "Name contains control characters"

    return True, ""


def even_id_inversion(servo_id: int, value: float) -> float:
    """Negate value for even-ID servos. Self-inverse; converts logical↔servo frame."""
    return -value if servo_id % 2 == 0 else value


def degrees_to_servo_radians(servo_id: int, deg_rel: float, middle_pos: float) -> float:
    """Logical degrees-relative-to-middle → servo-frame radians.

    Round-trip safe with ``servo_radians_to_degrees``.

    Parameters
    ----------
    servo_id:
        SCS0009 ID, 1-8. Even IDs trigger frame inversion.
    deg_rel:
        Logical degrees relative to calibrated middle (positive = close).
    middle_pos:
        Per-servo calibration offset in **servo-frame** degrees (the motor's
        reported angle at finger neutral).
    """
    deg_servo_rel = even_id_inversion(servo_id, deg_rel)
    return float(np.deg2rad(deg_servo_rel + middle_pos))


def servo_radians_to_degrees(servo_id: int, radians: float, middle_pos: float) -> float:
    """Servo-frame radians → logical degrees-relative-to-middle.

    Inverse of ``degrees_to_servo_radians``.
    """
    deg_servo_rel = float(np.rad2deg(radians)) - middle_pos
    return even_id_inversion(servo_id, deg_servo_rel)


def compose_finger(
    base: int,
    side: int,
    servo_min: int = -40,
    servo_max: int = 110,
) -> tuple[int, int]:
    """``(base, side)`` → ``(pos1, pos2)`` symmetric servo pair, clamped.

    Both outputs are in the **logical** frame (positive = close, same direction
    for both servos). pos1 takes ``base - side``, pos2 takes ``base + side``,
    so that ``decompose_finger`` round-trips cleanly. The simplified symmetric
    model replaces AmazingHandControl's bilinear ``compute_auto_positions``;
    we don't expose left/right "auto-extreme" interpolation in v1.
    """
    pos1 = clamp(base - side, servo_min, servo_max)
    pos2 = clamp(base + side, servo_min, servo_max)
    return int(pos1), int(pos2)


def decompose_finger(
    pos1: int,
    pos2: int,
    servo_min: int = -40,
    servo_max: int = 110,
    side_min: int = -40,
    side_max: int = 40,
) -> tuple[int, int]:
    """``(pos1, pos2)`` → ``(base, side)``. Inverse of ``compose_finger``."""
    p1 = clamp(int(pos1), servo_min, servo_max)
    p2 = clamp(int(pos2), servo_min, servo_max)
    base = (p1 + p2) // 2
    side = clamp((p2 - p1) // 2, side_min, side_max)
    return int(base), int(side)
