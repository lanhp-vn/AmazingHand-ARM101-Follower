"""Resolve a named hand pose to per-servo wire targets (pure; no hardware).

Two pose sources are unified here so every caller (the ``set_pose`` CLI, demos)
shares one decode path:

- Built-in ``open`` / ``close`` -- computed from each finger's calibrated
  ``base_min`` / ``base_max`` (logical frame, neutral spread), backed off the
  endpoint by ``margin_deg`` so the fingers never slam the measured limits.
- Any named pose stored in ``src/arm101_hand/data/hand_config.yaml`` (e.g.
  ``grab``, ``middle``) -- per-finger ``[servo_1, servo_2]`` pairs already in
  **servo frame** (even-ID pre-inverted, degrees relative to each servo's
  calibrated ``middle_pos``), so the wire angle is simply
  ``deg2rad(stored + middle_pos)``.

Output is ``{servo_id: wire_radians}`` ready for
``Scs0009PyController.write_goal_position``. Driving the bus is the caller's job
-- this module never touches hardware.
"""

from __future__ import annotations

from arm101_hand.config import HandCalibration, HandConfig
from arm101_hand.config.hand_config import HandPose
from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand.kinematics import (
    compose_finger,
    degrees_to_servo_radians,
    even_id_inversion,
)

#: Poses computed from the calibrated limits rather than stored in hand_config.yaml.
BUILTIN_POSES: tuple[str, ...] = ("open", "close")

#: Degrees to back the open/close poses off the calibrated endpoints (safety margin).
DEFAULT_POSE_MARGIN_DEG = 5.0


def available_pose_names(poses_cfg: HandConfig) -> list[str]:
    """Built-in poses plus every pose stored in ``hand_config.yaml`` (deduped, ordered)."""
    names = list(BUILTIN_POSES)
    for name in poses_cfg.poses:
        if name not in names:
            names.append(name)
    return names


def resolve_hand_pose_targets(
    calib: HandCalibration,
    poses_cfg: HandConfig,
    pose_name: str,
    *,
    margin_deg: float = DEFAULT_POSE_MARGIN_DEG,
) -> dict[int, float]:
    """``pose_name`` -> ``{servo_id: wire_radians}`` for all 8 servos.

    ``open``/``close`` take precedence over a same-named stored pose. Raises
    ``KeyError(pose_name)`` if the name is neither built-in nor present in
    ``poses_cfg``.
    """
    if pose_name in BUILTIN_POSES:
        return _resolve_builtin(calib, pose_name, margin_deg)
    if pose_name in poses_cfg.poses:
        return _resolve_stored(calib, poses_cfg.poses[pose_name])
    raise KeyError(pose_name)


def _resolve_builtin(calib: HandCalibration, pose_name: str, margin_deg: float) -> dict[int, float]:
    """``open``/``close``: every finger at ``base_min + m`` / ``base_max - m``, neutral spread."""
    targets: dict[int, float] = {}
    for finger_name, finger in calib.fingers.items():
        lim = finger.limits
        base = lim.base_max - margin_deg if pose_name == "close" else lim.base_min + margin_deg
        pos1, pos2 = compose_finger(int(round(base)), 0)  # logical frame; side = 0 (neutral spread)
        id1, id2 = FINGER_SERVO_IDS[finger_name]
        targets[id1] = degrees_to_servo_radians(id1, pos1, finger.servo_1.middle_pos)
        targets[id2] = degrees_to_servo_radians(id2, pos2, finger.servo_2.middle_pos)
    return targets


def _resolve_stored(calib: HandCalibration, pose: HandPose) -> dict[int, float]:
    """Decode a per-finger servo-frame pose (HandPose) to wire radians per servo."""
    targets: dict[int, float] = {}
    for finger_name, (s1_stored, s2_stored) in pose.by_finger().items():
        id1, id2 = FINGER_SERVO_IDS[finger_name]
        finger = calib.fingers[finger_name]
        for servo_id, stored, mid in (
            (id1, s1_stored, finger.servo_1.middle_pos),
            (id2, s2_stored, finger.servo_2.middle_pos),
        ):
            deg_logical = even_id_inversion(servo_id, stored)
            targets[servo_id] = degrees_to_servo_radians(servo_id, deg_logical, mid)
    return targets
