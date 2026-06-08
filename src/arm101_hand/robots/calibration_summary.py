"""Pure (no-bus) helpers for reading and summarizing the SO-ARM101 follower calibration.

Consumed by ``scripts/diagnostics/show_calib.py`` and ``scripts/calibration/so_arm101/set_pose.py``. Kept
free of serial I/O so it is unit-testable without hardware (test pyramid, IL §04).

The calibration JSON (``scripts/calibration/so_arm101/so101_follower.json``) stores raw
12-bit encoder steps per joint. lerobot's DEGREES normalization measures degrees relative
to the range MIDPOINT (see ``motors_bus._normalize``), so the usable degree window for a
joint is symmetric: ``[-span/2, +span/2]`` where ``span`` is the degree span.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# STS3215 encoder resolution (counts per full turn). lerobot uses ``resolution - 1`` in
# its degree conversion (``motors_bus._normalize`` / ``_unnormalize``).
STS3215_RESOLUTION = 4096

ARM_JOINTS: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)


@dataclass(frozen=True)
class JointCalib:
    """One joint's calibration in raw encoder steps."""

    id: int
    drive_mode: int
    homing_offset: int
    range_min: int
    range_max: int


def load_arm_calibration(path: Path) -> dict[str, JointCalib]:
    """Parse ``so101_follower.json`` into a ``JointCalib`` per joint.

    Raises ``ValueError`` if any of the five canonical joints is missing, or a joint
    is missing a required key.  The returned dict's keys follow ``ARM_JOINTS`` order.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [j for j in ARM_JOINTS if j not in raw]
    if missing:
        raise ValueError(f"calibration missing joints: {missing}")
    out: dict[str, JointCalib] = {}
    for joint in ARM_JOINTS:
        entry = raw[joint]
        try:
            jc = JointCalib(
                id=int(entry["id"]),
                drive_mode=int(entry["drive_mode"]),
                homing_offset=int(entry["homing_offset"]),
                range_min=int(entry["range_min"]),
                range_max=int(entry["range_max"]),
            )
        except (KeyError, TypeError) as e:
            raise ValueError(f"joint '{joint}' missing key or malformed entry: {e}") from e
        if jc.range_min >= jc.range_max:
            raise ValueError(
                f"joint '{joint}' invalid range: range_min ({jc.range_min}) >= range_max ({jc.range_max})"
            )
        out[joint] = jc
    return out


def degree_span(range_min: int, range_max: int, resolution: int = STS3215_RESOLUTION) -> float:
    """Full reachable span in degrees: ``(range_max - range_min) * 360 / (resolution - 1)``."""
    return (range_max - range_min) * 360.0 / (resolution - 1)


def midpoint_steps(range_min: int, range_max: int) -> float:
    """Midpoint of the calibrated range, in encoder steps."""
    return (range_min + range_max) / 2.0


def degree_bounds(
    range_min: int, range_max: int, resolution: int = STS3215_RESOLUTION
) -> tuple[float, float]:
    """Usable degree window relative to the range midpoint: ``(-span/2, +span/2)``."""
    half = degree_span(range_min, range_max, resolution) / 2.0
    return (-half, half)


def clamp_degrees(
    value: float, range_min: int, range_max: int, resolution: int = STS3215_RESOLUTION
) -> float:
    """Clamp a degree target to the joint's usable window."""
    lo, hi = degree_bounds(range_min, range_max, resolution)
    return max(lo, min(hi, value))
