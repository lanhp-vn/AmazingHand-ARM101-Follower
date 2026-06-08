"""Grab demo: drive the SO-ARM101 to its ``grab`` pose, then the AmazingHand to
its ``grab`` pose, hold both under torque, and release on Enter.

Sequence (arm first, then hand): position the wrist with the arm, then close the
hand into its grab. The two devices live on separate buses (arm = 12 V STS3215,
hand = 5 V SCS0009) on different COM ports, so both are held open at once -- no
shared rail (IL-1) and one owner per bus (IL-4).

On exit (Enter, Ctrl+C, or EOF): the arm is holding the raised grab pose and
would sag under gravity if simply released, so ``confirm_and_release`` prompts
'h' to drive it home first or Enter to release in place (both stay gripped during
the prompt). Choosing 'h' also opens the hand (to its ``open`` pose) as the arm
returns home; releasing in place leaves the hand where it is. Then the hand torque
is released. Torque on BOTH buses is always cut, even if one leg fails partway
through.

Poses come from version-controlled config (IL-5; never written here):
  * arm  -> data/arm_config.yaml ``poses['grab']``
  * hand -> src/arm101_hand/data/hand_config.yaml ``poses['grab']``

Usage:
  uv run python scripts/demos/grab_sequence.py
"""

from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_arm_poses, load_hand_calibration, load_hand_config
from arm101_hand.hand import resolve_hand_pose_targets
from arm101_hand.robots.calibration_summary import ARM_JOINTS, clamp_degrees, load_arm_calibration
from arm101_hand.scripts.device_setup import (
    ARM_CONFIG_PATH,
    CALIB_PATH,
    build_follower,
    confirm_and_release,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
)

# scripts/demos/grab_sequence.py -> repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
HAND_CALIB_PATH = _REPO_ROOT / "scripts" / "calibration" / "amazing_hand" / "hand_calib_values.yaml"
HAND_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "hand_config.yaml"

ARM_POSE = "grab"
HAND_POSE = "grab"
_ARM_SETTLE_S = 2.0
_HAND_SETTLE_S = 1.0


def _build_arm_targets() -> dict[str, float]:
    """Arm ``grab`` degrees per joint, clamped to the calibrated window (out-of-range skipped)."""
    poses = load_arm_poses(ARM_CONFIG_PATH).poses
    if ARM_POSE not in poses:
        raise SystemExit(f"arm pose {ARM_POSE!r} not in {ARM_CONFIG_PATH} (poses)")
    pose = poses[ARM_POSE].as_dict()
    calib = load_arm_calibration(CALIB_PATH)
    targets: dict[str, float] = {}
    for joint in ARM_JOINTS:
        raw = pose[joint]
        clamped = clamp_degrees(raw, calib[joint].range_min, calib[joint].range_max)
        if abs(clamped - raw) > 1e-6:
            print(f"  WARNING: arm {joint} target {raw:.1f} deg out of range -> skipped")
            continue
        targets[joint] = clamped
    if not targets:
        raise SystemExit("ERROR: all arm joints out of range -- nothing to drive")
    return targets


def _drive_hand(c: Scs0009PyController, targets: dict[int, float], speed: int) -> None:
    """Enable torque and command every servo to its wire-radians target at ``speed``."""
    for sid in targets:
        c.write_torque_enable(sid, 1)
    for sid in sorted(targets):
        c.write_goal_speed(sid, speed)
        time.sleep(0.0002)
    for sid in sorted(targets):
        c.write_goal_position(sid, targets[sid])
        time.sleep(0.0002)


def main() -> int:
    # Resolve BOTH poses before touching any hardware -- fail fast, leave no bus open.
    arm_targets = _build_arm_targets()

    hand_calib = load_hand_calibration(HAND_CALIB_PATH)
    hand_cfg = load_hand_config(HAND_CONFIG_PATH) if HAND_CONFIG_PATH.is_file() else None
    if hand_cfg is None or HAND_POSE not in hand_cfg.poses:
        raise SystemExit(f"hand pose {HAND_POSE!r} not in {HAND_CONFIG_PATH} (poses)")
    hand_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, HAND_POSE)
    # Open targets are built-in (limits-derived); used only if the user homes on exit.
    hand_open_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, "open")

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)  # DEGREES
    vel = gentle_velocity(cfg)
    home = load_home_degrees()

    hand = Scs0009PyController(
        serial_port=hand_cfg.connection.port,
        baudrate=hand_cfg.connection.baudrate,
        timeout=hand_cfg.connection.timeout,
    )

    def _open_hand_on_home() -> None:
        """Bring the hand to 'open' when the user chooses to home the arm on exit."""
        print(f"Opening hand to 'open' on {hand_cfg.connection.port} ...")
        _drive_hand(hand, hand_open_targets, hand_cfg.tuning.speeds.open)
        time.sleep(_HAND_SETTLE_S)

    arm_torque_on = False
    try:
        # ---- Arm leg: connect, push on-file calibration, gentle velocity, drive grab ----
        print(f"Connecting arm on {cfg.arm.port}; driving to '{ARM_POSE}' ...")
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open arm port {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        follower.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, vel))
        follower.bus.enable_torque()
        arm_torque_on = True
        follower.send_action({f"{j}.pos": v for j, v in arm_targets.items()})
        time.sleep(_ARM_SETTLE_S)
        print(f"Arm holding '{ARM_POSE}': {arm_targets}")

        # ---- Hand leg: close into grab ----
        print(f"Driving hand on {hand_cfg.connection.port} to '{HAND_POSE}' ...")
        _drive_hand(hand, hand_targets, hand_cfg.tuning.speed)
        time.sleep(_HAND_SETTLE_S)
        print(f"Arm + hand holding '{ARM_POSE}'/'{HAND_POSE}' under torque.")
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- exiting")
    finally:
        # Release the arm first (it prompts 'h'/Enter; both stay gripped during the
        # prompt, then the arm torque comes off). The hand release lives in an inner
        # ``finally`` so it ALWAYS runs -- even if the arm leg raises (including a
        # Ctrl+C, which contextlib.suppress would NOT catch). Neither bus may be left
        # torqued (IL-4). confirm_and_release is deliberately NOT suppressed: a real
        # arm disable_torque() failure should surface, not be hidden behind a false
        # "Arm bus closed."
        try:
            if follower.is_connected:
                confirm_and_release(
                    follower, arm_torque_on, home, vel, offer_home=True, on_home=_open_hand_on_home
                )
                with contextlib.suppress(Exception):
                    follower.disconnect()
                print("Arm bus closed.")
        finally:
            print("Releasing hand torque.")
            for sid in hand_targets:
                with contextlib.suppress(Exception):
                    hand.write_torque_enable(sid, 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
