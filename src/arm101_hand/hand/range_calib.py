"""Pure jog-to-limit state machine for AmazingHand range calibration.

No hardware, no ``msvcrt`` — this is the testable core of
``scripts/calibration/amazing_hand/range_calib.py``. The script reads
raw keys, maps them via ``key_to_action``, advances a ``JogState`` via
``apply_action``, then composes the live ``(base, side)`` cursor into servo
commands (using ``hand.kinematics.compose_finger`` +
``degrees_to_servo_radians``) and writes them over the bus.

Frame: ``base``/``side`` are the **logical** DOF (see ``hand.kinematics``).
During calibration the cursor roams a generous *safety* envelope
(``JOG_BASE_*`` / ``JOG_SIDE_*``) — deliberately wider than any expected
limit, because the whole point is to discover where the real stops are. The
true protections are (a) the per-servo clamp inside ``compose_finger`` at write
time and (b) the operator watching the ``present_load`` warning.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# Jog step bounds (degrees). Conservative default; operator widens/narrows live.
STEP_DEFAULT = 5
STEP_MIN = 1
STEP_MAX = 15

# Generous cursor safety envelope (degrees, logical frame). Wider than any real
# stored limit so the operator can jog *to* the mechanical stop and mark it.
JOG_BASE_MIN = -60
JOG_BASE_MAX = 130
JOG_SIDE_MIN = -60
JOG_SIDE_MAX = 60

# present_load magnitude above which we warn. The SCS0009 "Present Load"
# register (addr 60) is NOT torque/current: rustypot returns a signed magnitude
# in raw counts where the 0..1000 field is the motor's PWM/voltage duty in tenths
# of a percent (1000 = 100.0%). This threshold (~8% drive duty) was set from
# bench observation of the AmazingHand's small SCS0009s. Feetech documents no
# numeric stall value, so confirm on the bench (compare the load=[..] line during
# free jog vs. pushed into a stop) and adjust.
LOAD_WARN_THRESHOLD = 80

# Raw-key → action. Arrow names are produced by the msvcrt shell in Task 5.
_KEY_ACTIONS: dict[str, str] = {
    "UP": "base+",
    "DOWN": "base-",
    "RIGHT": "side+",
    "LEFT": "side-",
    "[": "step-",
    "]": "step+",
    "1": "mark_base_min",
    "2": "mark_base_max",
    "3": "mark_side_min",
    "4": "mark_side_max",
    "h": "home",
    "s": "save",
    "q": "quit",
}

# action → (limit_name) for the four mark actions.
_MARK_TARGETS: dict[str, str] = {
    "mark_base_min": "base_min",
    "mark_base_max": "base_max",
    "mark_side_min": "side_min",
    "mark_side_max": "side_max",
}


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _clamp_f(value: int, lo: float, hi: float) -> int:
    """Clamp an int value to float bounds, returning int."""
    return int(max(lo, min(hi, value)))


@dataclass(frozen=True)
class JogState:
    """Immutable jog cursor: where we are and how big each step is."""

    base: int = 0
    side: int = 0
    step: int = STEP_DEFAULT


def key_to_action(key: str) -> str | None:
    """Map a normalized key token to an action name, or ``None`` if unmapped."""
    return _KEY_ACTIONS.get(key)


def apply_action(
    state: JogState,
    action: str,
    *,
    step_min: int = STEP_MIN,
    step_max: int = STEP_MAX,
    jog_base_min: float = JOG_BASE_MIN,
    jog_base_max: float = JOG_BASE_MAX,
    jog_side_min: float = JOG_SIDE_MIN,
    jog_side_max: float = JOG_SIDE_MAX,
) -> tuple[JogState, tuple[str, int] | None]:
    """Apply an action to the cursor.

    Returns ``(new_state, mark)`` where ``mark`` is ``None`` except for the four
    mark actions, which return ``(limit_name, captured_value)`` and leave the
    cursor unchanged. ``home`` / ``save`` / ``quit`` are handled by the caller;
    here ``home`` resets the cursor and ``save``/``quit`` are no-ops on state.

    The jog bounds default to the module-level constants so callers that do not
    pass bounds (e.g. ``pose_jog``) keep working without change. Pass
    ``step_min``/``step_max`` (``int``) and ``jog_base_*``/``jog_side_*``
    (``float``) from ``hcfg.tuning`` to wire the config knobs.
    """
    if action == "base+":
        return replace(state, base=_clamp_f(state.base + state.step, jog_base_min, jog_base_max)), None
    if action == "base-":
        return replace(state, base=_clamp_f(state.base - state.step, jog_base_min, jog_base_max)), None
    if action == "side+":
        return replace(state, side=_clamp_f(state.side + state.step, jog_side_min, jog_side_max)), None
    if action == "side-":
        return replace(state, side=_clamp_f(state.side - state.step, jog_side_min, jog_side_max)), None
    if action == "step+":
        return replace(state, step=_clamp(state.step + 1, step_min, step_max)), None
    if action == "step-":
        return replace(state, step=_clamp(state.step - 1, step_min, step_max)), None
    if action == "home":
        return replace(state, base=0, side=0), None
    if action in _MARK_TARGETS:
        name = _MARK_TARGETS[action]
        value = state.base if name.startswith("base") else state.side
        return state, (name, value)
    # save / quit / anything else: no state change.
    return state, None


def format_status(state: JogState, load1: int, load2: int) -> str:
    """One-line live status for the REPL."""
    return f"base={state.base:>4}  side={state.side:>4}  step={state.step:>2}  load=[{load1},{load2}]"


def load_warning(load1: int, load2: int, threshold: int = LOAD_WARN_THRESHOLD) -> str | None:
    """Return a warning string if either servo's |load| exceeds ``threshold``."""
    hot = [sid for sid, load in ((1, load1), (2, load2)) if abs(load) > threshold]
    if not hot:
        return None
    return f"WARNING: high load on servo(s) {hot} — back off one step and mark there"
