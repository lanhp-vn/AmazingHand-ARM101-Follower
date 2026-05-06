"""AmazingHand device layer (rustypot-based) and pure-math kinematics."""

from .kinematics import (
    MAX_NAME_LEN,
    clamp,
    compose_finger,
    decompose_finger,
    degrees_to_servo_radians,
    even_id_inversion,
    servo_radians_to_degrees,
    validate_pose_name,
)

__all__ = [
    "MAX_NAME_LEN",
    "clamp",
    "compose_finger",
    "decompose_finger",
    "degrees_to_servo_radians",
    "even_id_inversion",
    "servo_radians_to_degrees",
    "validate_pose_name",
]
