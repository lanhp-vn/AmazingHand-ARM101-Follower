"""Shared application-layer glue for SO-ARM101 device/bus construction.

Promoted out of ``scripts/calibration/so_arm101/_common.py`` so both the arm
calibration scripts AND the device-neutral ``scripts/diagnostics/`` helpers can
import it without a ``sys.path`` hack. Not pure (loads config + constructs the
robot/bus); the unit-testable calibration math lives in
``arm101_hand.robots.calibration_summary``.

IL-3: motors 1-5 only (no gripper ID 6). IL-4: callers own the bus for the
duration of one run. IL-5: reads ``so101_follower.json``; never writes it.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

from arm101_hand.config import ArmTuning, load_arm_config
from arm101_hand.robots.calibration_summary import ARM_JOINTS, STS3215_RESOLUTION

# src/arm101_hand/scripts/device_setup.py -> repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
ARM_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "arm_config.yaml"
CALIB_PATH = _REPO_ROOT / "scripts" / "calibration" / "so_arm101" / "so101_follower.json"

# id="so101_follower" -> SO101FollowerNoGripper loads <calibration_dir>/so101_follower.json,
# and the subclass defaults calibration_dir to that directory.
FOLLOWER_ID = "so101_follower"


def load_arm_app_config():
    """Load + validate src/arm101_hand/data/arm_config.yaml (SystemExit with a clear message if absent)."""
    if not ARM_CONFIG_PATH.is_file():
        raise SystemExit(f"arm_config.yaml not found at {ARM_CONFIG_PATH}")
    return load_arm_config(ARM_CONFIG_PATH)


def build_follower(cfg, *, use_degrees: bool):
    """Construct an (unconnected) SO101FollowerNoGripper from ``cfg``."""
    from arm101_hand.robots import SO101FollowerNoGripper, SO101FollowerNoGripperConfig

    robot_cfg = SO101FollowerNoGripperConfig(
        port=cfg.connection.port,
        id=FOLLOWER_ID,
        use_degrees=use_degrees,
    )
    return SO101FollowerNoGripper(robot_cfg)


def build_raw_bus(cfg):
    """Construct an (unconnected) raw 5-motor FeetechMotorsBus for read-only scanning."""
    from lerobot.motors import Motor, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus

    motors = {
        name: Motor(motor_id, "sts3215", MotorNormMode.DEGREES)
        for motor_id, name in enumerate(ARM_JOINTS, start=1)
    }
    return FeetechMotorsBus(port=cfg.connection.port, motors=motors)


def gentle_velocity(cfg) -> int:
    """Goal_Velocity (STS3215 register 46, raw units) for gentle calibration moves."""
    return cfg.tuning.park_velocity


def load_home_degrees() -> dict[str, float]:
    """Per-joint default-home target in degrees, from arm_config.yaml ``poses['home']``."""
    if ARM_CONFIG_PATH.is_file():
        home = load_arm_config(ARM_CONFIG_PATH).poses.get("home")
        if home is not None:
            return home.as_dict()
    return dict.fromkeys(ARM_JOINTS, 0.0)


def safe_enable_torque(follower, vel: int) -> None:
    """Enable arm torque WITHOUT the power-up lunge: seed Goal_Position<-Present + cap Goal_Velocity first.

    On a freshly-powered STS3215 the SRAM ``Goal_Velocity`` register defaults to 0, which in
    position mode means *maximum* speed (not "stop"), and ``Goal_Position`` holds a stale value.
    A bare ``enable_torque()`` therefore snaps every joint to that stale goal at full speed -- the
    violent first-run-after-power-up move. SRAM resets on every power-cycle, which is why only the
    first run(s) right after plugging in are affected (later runs in the same power session inherit
    the gentle values left in RAM). Seeding ``Goal_Position <- Present_Position`` and writing a
    ``vel`` cap to ``Goal_Velocity`` *before* enabling torque makes torque-on simply HOLD the
    current pose gently. Both writes happen with torque off, so nothing moves until ``enable_torque``.

    Mirrors the no-jump pattern already used by jog.py (``_hold_at``) and capture_pose.py; lerobot's
    own ``enable_torque`` does neither (writes only Torque_Enable + Lock), so we add it here (IL-2).
    """
    present = follower.bus.sync_read("Present_Position", normalize=False)
    follower.bus.sync_write("Goal_Velocity", dict.fromkeys(present, vel))
    follower.bus.sync_write("Goal_Position", present, normalize=False)
    follower.bus.enable_torque()


def drive_arm_joints(
    follower,
    targets_deg: dict[str, float],
    vel: int,
    *,
    tolerance: int = 25,
    timeout_s: float = 8.0,
    poll_s: float = 0.05,
) -> None:
    """Drive a SUBSET of joints to degree targets, then WAIT until they arrive. No torque change.

    ``targets_deg`` maps joint name -> degrees relative to that joint's calibrated mid; only the
    joints present are commanded (the rest hold their current torqued position). Degrees are
    converted to raw encoder steps and written with ``normalize=False`` so it works for ANY
    follower norm mode (DEGREES or RANGE_M100_100). After commanding, polls ``Present_Position``
    until every commanded joint is within ``tolerance`` raw steps of its goal -- so a slow, gentle
    move actually completes before the caller proceeds. Gives up after ``timeout_s`` (prints a
    note) so it never hangs. An empty ``targets_deg`` is a no-op. The caller owns enable/disable.
    """
    if not targets_deg:
        return
    goal_raw: dict[str, int] = {}
    for joint, deg in targets_deg.items():
        cal = follower.calibration[joint]
        mid = (cal.range_min + cal.range_max) / 2
        goal_raw[joint] = int(round(mid + deg * (STS3215_RESOLUTION - 1) / 360))
    follower.bus.sync_write("Goal_Velocity", dict.fromkeys(goal_raw, vel))
    follower.bus.sync_write("Goal_Position", goal_raw, normalize=False)

    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        present = follower.bus.sync_read("Present_Position", normalize=False)
        if all(abs(present[j] - goal_raw[j]) <= tolerance for j in goal_raw):
            return
        time.sleep(poll_s)
    print("  (target not fully reached within timeout -- continuing)", file=sys.stderr)


def _drive_home(
    follower,
    home: dict[str, float],
    vel: int,
    tolerance: int = 25,
    timeout_s: float = 8.0,
    poll_s: float = 0.05,
) -> None:
    """Drive ALL joints to the ``home`` pose (degrees) and wait. Thin wrapper over drive_arm_joints."""
    drive_arm_joints(
        follower,
        {j: home[j] for j in ARM_JOINTS},
        vel,
        tolerance=tolerance,
        timeout_s=timeout_s,
        poll_s=poll_s,
    )


def confirm_and_release(
    follower,
    torque_on: bool,
    home: dict[str, float],
    vel: int,
    *,
    offer_home: bool = True,
    on_home: Callable[[], None] | None = None,
    home_routine: Callable[[], None] | None = None,
    tuning: ArmTuning | None = None,
) -> None:
    """On exit, release torque after handling the home pose (never auto-homes a held pose).

    - ``offer_home=True`` (default): if the arm is holding a pose under torque, prompt -- type
      ``h`` to drive back to the configured home before releasing, or just Enter to release in
      place (it will sag under gravity from a raised pose). Ctrl+C / EOF releases in place.
    - ``offer_home=False``: the caller is already parking AT home (e.g. ``set_pose.py home``),
      so there is nothing to "return" to -- just settle at home (wait until reached) and
      release, with no prompt.

    ``on_home`` is an optional hook invoked only when the user chooses to home (the ``h``
    branch), after the arm reaches home and before its torque is cut. A coordinated caller
    (e.g. the grab demo) uses it to bring a second device home too. Its failure is reported
    but never blocks the arm torque-off.

    ``home_routine`` (optional): when supplied, the ``h`` branch calls it INSTEAD of the built-in
    "drive all joints home, then run on_home" -- a coordinated caller (e.g. the grab demo) uses it
    to run a fully custom, staged return (e.g. open the hand, then reverse the arm stage by stage).
    Its failure is reported but never blocks the arm torque-off. ``on_home`` is ignored when
    ``home_routine`` is set.

    ``tuning`` (optional ``ArmTuning``): when supplied, overrides ``_drive_home``'s
    tolerance/timeout/poll defaults with values from ``arm_config.yaml``. If ``None``,
    the compiled-in defaults (25 steps / 8.0 s / 0.05 s) are used unchanged.

    Torque is ALWAYS disabled before returning (IL-4). If torque is already off, just ensures it.
    """
    # Build overrides from tuning if provided; otherwise _drive_home uses its own defaults.
    drive_kw: dict[str, object] = {}
    if tuning is not None:
        drive_kw = {
            "tolerance": tuning.home_tolerance_steps,
            "timeout_s": tuning.home_timeout_s,
            "poll_s": tuning.home_poll_s,
        }

    if torque_on and not offer_home:
        print("Settling at home, then releasing torque ...")
        try:
            _drive_home(follower, home, vel, **drive_kw)  # type: ignore[arg-type]
        except BaseException as e:
            print(f"  (settle interrupted: {e!r}) -- releasing anyway", file=sys.stderr)
    elif torque_on:
        print(
            "\nReminder: the arm is holding its pose under torque and will SAG under gravity when released."
        )
        try:
            choice = (
                input("Type 'h' + Enter to return home first, or just Enter to release in place: ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            choice = ""
        if choice == "h":
            if home_routine is not None:
                print("Returning home (staged reverse) ...")
                try:
                    home_routine()
                except BaseException as e:
                    print(
                        f"  (staged reverse interrupted: {e!r}) -- releasing torque anyway",
                        file=sys.stderr,
                    )
            else:
                print("Returning to home ...")
                try:
                    _drive_home(follower, home, vel, **drive_kw)  # type: ignore[arg-type]
                    print("Home reached.")
                except BaseException as e:
                    print(f"  (homing interrupted: {e!r}) -- releasing torque anyway", file=sys.stderr)
                if on_home is not None:
                    try:
                        on_home()
                    except BaseException as e:
                        print(f"  (on_home hook failed: {e!r})", file=sys.stderr)
    follower.bus.disable_torque()
