"""Pure two-state toggle core for ``scripts/demos/grab_toggle.py``.

No hardware, no ``msvcrt`` -- the testable core of the index-finger "button click".
After the grab demo settles, the index finger alternates between an OUT base (where
``grab`` left it) and an IN base (OUT + ``delta``, clamped to the finger's calibrated
``base_max``). Mirrors the pure-core / thin-shell split of ``pose_jog`` and
``range_calib``: this module is the state machine; the script owns keys and the bus.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from arm101_hand.hand.kinematics import clamp

# Toggle delta bounds (degrees). Default 20 matches the bench example (grab base 33 ->
# pressed 53). The real ceiling on press depth is the index ``base_max`` enforced by
# ``in_base``'s clamp, so these bounds are deliberately independent of the jog STEP_*.
TOGGLE_DELTA_DEFAULT = 20
TOGGLE_DELTA_MIN = 1
TOGGLE_DELTA_MAX = 40

_KEY_ACTIONS: dict[str, str] = {
    " ": "toggle",
    "[": "delta-",
    "]": "delta+",
    "q": "quit",
}


@dataclass(frozen=True)
class ToggleState:
    """Immutable index-toggle cursor.

    ``out_base``/``side`` are the logical ``(base, side)`` the index settled at in
    ``grab``; ``delta`` is the live press depth; ``pressed`` is True when clicked IN.
    """

    out_base: int
    side: int
    delta: int = TOGGLE_DELTA_DEFAULT
    pressed: bool = False


def key_to_action(key: str) -> str | None:
    """Map a raw key token to a toggle action, or ``None`` if unmapped."""
    return _KEY_ACTIONS.get(key)


def in_base(state: ToggleState, base_min: int, base_max: int) -> int:
    """The clicked-IN base: ``out_base + delta``, clamped to the calibrated window."""
    return int(clamp(state.out_base + state.delta, base_min, base_max))


def target_base(state: ToggleState, base_min: int, base_max: int) -> int:
    """Base the index should hold now: IN base when ``pressed``, OUT base otherwise."""
    if state.pressed:
        return in_base(state, base_min, base_max)
    return int(clamp(state.out_base, base_min, base_max))


def apply_action(state: ToggleState, action: str) -> ToggleState:
    """Apply an action. ``quit`` / unknown are state no-ops (handled by the shell).

    Delta keys adjust the press depth only -- they never flip ``pressed`` -- so the
    finger never moves on a ``[`` / ``]`` keypress (no surprise movements).
    """
    if action == "toggle":
        return replace(state, pressed=not state.pressed)
    if action == "delta+":
        return replace(state, delta=int(clamp(state.delta + 1, TOGGLE_DELTA_MIN, TOGGLE_DELTA_MAX)))
    if action == "delta-":
        return replace(state, delta=int(clamp(state.delta - 1, TOGGLE_DELTA_MIN, TOGGLE_DELTA_MAX)))
    return state
