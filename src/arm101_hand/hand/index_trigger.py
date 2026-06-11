# src/arm101_hand/hand/index_trigger.py
"""Pure one-shot trigger-cycle state for ``scripts/demos/grab_trigger_capture.py``.

Like ``index_toggle`` (the button click), but a single SPACE 'fires' a full
press->hold->release cycle rather than latching a pressed/unpressed flag. The press
geometry (out_base + delta, clamped to the calibrated window) is reused from
``index_toggle.in_base`` -- one source for the index press depth (IL-7). The script
owns the keys, the bus, and the hold dwell; this module is the depth cursor + key map.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from arm101_hand.hand.index_toggle import (
    TOGGLE_DELTA_DEFAULT,
    TOGGLE_DELTA_MAX,
    TOGGLE_DELTA_MIN,
    ToggleState,
    in_base,
)
from arm101_hand.hand.kinematics import clamp

_KEY_ACTIONS: dict[str, str] = {" ": "fire", "[": "delta-", "]": "delta+", "q": "quit"}


@dataclass(frozen=True)
class TriggerState:
    """Index press-depth cursor: settled OUT ``(out_base, side)`` plus the live ``delta``."""

    out_base: int
    side: int
    delta: int = TOGGLE_DELTA_DEFAULT


def key_to_action(key: str) -> str | None:
    return _KEY_ACTIONS.get(key)


def press_base(state: TriggerState, base_min: int, base_max: int) -> int:
    """The IN base for a press: ``out_base + delta`` clamped to the calibrated window."""
    return in_base(ToggleState(out_base=state.out_base, side=state.side, delta=state.delta), base_min, base_max)


def apply_action(state: TriggerState, action: str) -> TriggerState:
    """Apply a key action. ``fire``/``quit``/unknown are state no-ops (handled by the shell);
    only the delta keys change state, and they never move the finger (no surprise movement)."""
    if action == "delta+":
        return replace(state, delta=int(clamp(state.delta + 1, TOGGLE_DELTA_MIN, TOGGLE_DELTA_MAX)))
    if action == "delta-":
        return replace(state, delta=int(clamp(state.delta - 1, TOGGLE_DELTA_MIN, TOGGLE_DELTA_MAX)))
    return state
