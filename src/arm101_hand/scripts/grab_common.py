"""Shared staged grab/reverse/release for the grab demos (application layer).

Both ``scripts/demos/grab_sequence.py`` and ``scripts/demos/grab_toggle.py`` drive the
same staged grab; this module owns it so the logic lives once (IL-7).

Forward sequence (each stage position-confirmed -- arm AND hand -- before the next):
  1. shoulder_pan -> base_max (its calibrated upper bound), AND hand -> pre_grab
     (commanded together, then both polled to completion)
  2. shoulder_lift + elbow_flex + wrist_flex + wrist_roll -> grab
  3. shoulder_pan -> grab
  4. hand fingers -> grab (closes onto the object; stalls -> times out -> continues)

Once both devices hold ``grab`` under torque, ``run_grab_demo`` calls the optional
``on_hold(ctx)`` hook (e.g. an interactive index-toggle loop) before the exit prompt.

The two devices live on separate buses (arm = 12 V STS3215, hand = 5 V SCS0009) on
different COM ports, so both are held open at once -- no shared rail (IL-1) and one
owner per bus (IL-4).

On exit:
  * plain Enter / Ctrl+C / EOF -> release BOTH in place (no movement); the arm will
    sag from a raised pose -- you are warned at the prompt.
  * 'h' -> run the sequence in REVERSE: hand -> pre_grab, then shoulder_pan -> base_max,
    then the four posture joints -> home, then shoulder_pan -> home, then (arm now at
    home) hand -> open. Each move is position-confirmed. Torque on BOTH buses is always
    cut at the end, even if a leg fails partway through.

Poses come from version-controlled config (IL-5; never written here):
  * arm  -> src/arm101_hand/data/arm_config.yaml ``poses['grab']`` / ``poses['home']``
  * hand -> src/arm101_hand/data/hand_config.yaml ``poses['grab']`` / ``poses['pre_grab']``
            (``open`` is built-in / limits-derived)
"""

from __future__ import annotations

import contextlib
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import (
    HandCalibration,
    HandConfig,
    load_arm_config,
    load_hand_calibration,
    load_hand_config,
)
from arm101_hand.hand import (
    drive_hand_servos,
    resolve_hand_pose_targets,
    wait_hand_reached,
    write_hand_servos,
)
from arm101_hand.robots.calibration_summary import (
    ARM_JOINTS,
    clamp_degrees,
    degree_bounds,
    load_arm_calibration,
)
from arm101_hand.scripts.device_setup import (
    ARM_CONFIG_PATH,
    CALIB_PATH,
    build_follower,
    confirm_and_release,
    drive_arm_joints,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
)

# src/arm101_hand/scripts/grab_common.py -> repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
HAND_CALIB_PATH = _REPO_ROOT / "scripts" / "calibration" / "amazing_hand" / "hand_calib_values.yaml"
HAND_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "hand_config.yaml"

ARM_POSE = "grab"
HAND_POSE = "grab"
HAND_PRE_GRAB_POSE = "pre_grab"
HAND_OPEN_POSE = "open"
PAN_JOINT = "shoulder_pan"
# Everything except the base pan -- moved together in forward step 2 / reverse step R2.
POSTURE_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")


@dataclass
class GrabHoldContext:
    """What an ``on_hold`` hook needs while both devices hold ``grab`` under torque.

    No arm handle is exposed on purpose -- a hold hook coordinates the hand only; the
    arm stays parked under torque and is released by ``run_grab_demo``'s exit path.
    """

    hand: Scs0009PyController
    hand_calib: HandCalibration
    hand_cfg: HandConfig


def build_arm_plan() -> tuple[dict[str, float], float]:
    """Return (clamped ``grab`` targets per joint, shoulder_pan base_max in degrees).

    Grab degrees are clamped to each joint's calibrated window (an out-of-range joint is
    warned and skipped). ``base_max`` is shoulder_pan's calibrated upper degree bound.
    """
    poses = load_arm_config(ARM_CONFIG_PATH).poses
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
    pan = calib[PAN_JOINT]
    base_max_deg = degree_bounds(pan.range_min, pan.range_max)[1]
    return targets, base_max_deg


def run_grab_demo(on_hold: Callable[[GrabHoldContext], None] | None = None) -> int:
    """Stage the arm+hand into ``grab``, hold under torque, then reverse on exit.

    When ``on_hold`` is given, it is called once -- after both devices reach ``grab`` and
    before the exit prompt -- with a ``GrabHoldContext``. With ``on_hold=None`` the
    behavior is identical to the original ``grab_sequence`` demo.
    """
    # Resolve everything before touching hardware -- fail fast, leave no bus open.
    arm_targets, base_max_deg = build_arm_plan()

    hand_calib = load_hand_calibration(HAND_CALIB_PATH)
    hand_cfg = load_hand_config(HAND_CONFIG_PATH) if HAND_CONFIG_PATH.is_file() else None
    if hand_cfg is None or HAND_POSE not in hand_cfg.poses:
        raise SystemExit(f"hand pose {HAND_POSE!r} not in {HAND_CONFIG_PATH} (poses)")
    if HAND_PRE_GRAB_POSE not in hand_cfg.poses:
        raise SystemExit(f"hand pose {HAND_PRE_GRAB_POSE!r} not in {HAND_CONFIG_PATH} (poses)")
    hand_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, HAND_POSE)
    hand_pre_grab_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, HAND_PRE_GRAB_POSE)
    # Open targets are built-in (limits-derived); used as the final reverse step.
    hand_open_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, HAND_OPEN_POSE)

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)  # DEGREES
    vel = gentle_velocity(cfg)
    home = load_home_degrees()
    arm_tuning = cfg.tuning
    # The home_* tuning (tolerance/timeout/poll) governs every staged arm move here, not just
    # homing -- the grab stages use the same position-wait, so we reuse it rather than add new keys.
    wait_kw = {
        "tolerance": arm_tuning.home_tolerance_steps,
        "timeout_s": arm_tuning.home_timeout_s,
        "poll_s": arm_tuning.home_poll_s,
    }
    # Hand position-poll params. tolerance reuses pose_margin_deg (no separate poll-tolerance knob
    # yet -- the same pragmatic reuse the arm makes with home_* above); timeout/poll from config.
    hand_wait_kw = {
        "tolerance_rad": math.radians(hand_cfg.tuning.pose_margin_deg),
        "timeout_s": hand_cfg.tuning.pose_timeout_s,
        "poll_s": hand_cfg.tuning.pose_poll_s,
    }
    # Forward pre_grab (step 2) uses the general jog speed; the grab CLOSE (step 4) uses the
    # dedicated flexion speed; opening moves (reverse pre_grab + open) use the extension speed.
    hand_speed = hand_cfg.tuning.speed
    hand_close_speed = hand_cfg.tuning.speeds.close
    hand_open_speed = hand_cfg.tuning.speeds.open

    # Per-stage arm joint subsets (degrees). Built from arm_targets so a skipped joint drops out.
    posture_grab = {j: arm_targets[j] for j in POSTURE_JOINTS if j in arm_targets}
    posture_home = {j: home[j] for j in POSTURE_JOINTS}
    pan_grab = {PAN_JOINT: arm_targets[PAN_JOINT]} if PAN_JOINT in arm_targets else {}
    pan_base_max = {PAN_JOINT: base_max_deg}
    pan_home = {PAN_JOINT: home[PAN_JOINT]}

    hand = Scs0009PyController(
        serial_port=hand_cfg.connection.port,
        baudrate=hand_cfg.connection.baudrate,
        timeout=hand_cfg.connection.timeout,
    )

    def _staged_reverse() -> None:
        """Reverse of the forward grab, run when the user types 'h' on exit.

        hand -> pre_grab, then arm pan->base_max / posture->home / pan->home, then (arm at
        home) hand -> open. The hand holds each pose under torque between stages; both torques
        are cut by the caller (confirm_and_release for the arm, the outer finally for the hand).
        """
        print(f"  [Rh] hand -> '{HAND_PRE_GRAB_POSE}' on {hand_cfg.connection.port} ...")
        drive_hand_servos(hand, hand_pre_grab_targets, hand_open_speed, **hand_wait_kw)  # type: ignore[arg-type]
        print(f"  [R3] shoulder_pan -> base_max ({base_max_deg:.1f} deg) ...")
        drive_arm_joints(follower, pan_base_max, vel, **wait_kw)
        print("  [R2] shoulder_lift/elbow_flex/wrist_flex/wrist_roll -> home ...")
        drive_arm_joints(follower, posture_home, vel, **wait_kw)
        print("  [R1] shoulder_pan -> home ...")
        drive_arm_joints(follower, pan_home, vel, **wait_kw)
        print(f"  [Ro] arm home -- hand -> '{HAND_OPEN_POSE}' ...")
        drive_hand_servos(hand, hand_open_targets, hand_open_speed, **hand_wait_kw)  # type: ignore[arg-type]

    arm_torque_on = False
    try:
        # ---- Arm leg: connect, push on-file calibration, enable torque ----
        print(f"Connecting arm on {cfg.connection.port} ...")
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open arm port {cfg.connection.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        follower.bus.enable_torque()
        arm_torque_on = True

        # ---- Forward grab, staged ----
        # Step 1: command shoulder_pan -> base_max and the hand -> pre_grab together, then
        # confirm BOTH (the hand opens to pre_grab while the base rotates).
        print(
            f"  [1] shoulder_pan -> base_max ({base_max_deg:.1f} deg)"
            f" + hand -> '{HAND_PRE_GRAB_POSE}' (together) ..."
        )
        write_hand_servos(hand, hand_pre_grab_targets, hand_speed)
        drive_arm_joints(follower, pan_base_max, vel, **wait_kw)
        wait_hand_reached(hand, hand_pre_grab_targets, **hand_wait_kw)

        # Step 2: arm posture -> grab (the hand is already holding pre_grab).
        print("  [2] posture -> grab ...")
        drive_arm_joints(follower, posture_grab, vel, **wait_kw)

        if not pan_grab:
            print("  WARNING: shoulder_pan out of range -- [3] is a no-op; pan stays at base_max")
        print("  [3] shoulder_pan -> grab ...")
        drive_arm_joints(follower, pan_grab, vel, **wait_kw)
        print(f"Arm holding '{ARM_POSE}': {arm_targets}")

        # ---- Hand leg: close into grab (stalls on the object -> timeout -> continue) ----
        print(f"  [4] hand -> '{HAND_POSE}' on {hand_cfg.connection.port} ...")
        drive_hand_servos(hand, hand_targets, hand_close_speed, **hand_wait_kw)  # type: ignore[arg-type]
        print(f"Arm + hand holding '{ARM_POSE}'/'{HAND_POSE}' under torque.")

        # ---- Optional interactive hold (e.g. index-toggle) ----
        if on_hold is not None:
            on_hold(GrabHoldContext(hand=hand, hand_calib=hand_calib, hand_cfg=hand_cfg))
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- exiting")
    finally:
        # Release the arm first: confirm_and_release prompts 'h'/Enter (both stay gripped
        # during the prompt). 'h' runs _staged_reverse; plain Enter releases in place. The hand
        # release lives in an inner finally so it ALWAYS runs -- neither bus may be left torqued (IL-4).
        try:
            if follower.is_connected:
                confirm_and_release(
                    follower,
                    arm_torque_on,
                    home,
                    vel,
                    offer_home=True,
                    home_routine=_staged_reverse,
                    tuning=arm_tuning,
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
