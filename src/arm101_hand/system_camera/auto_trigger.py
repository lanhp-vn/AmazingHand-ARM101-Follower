"""Auto-trigger lifecycle for the arc-driven Aurora capture (device layer, pure + clock-injected).

Drives the transition: wait for the alignment arcs to go GREEN -> require it stable for
``stable_seconds`` -> fire ONE capture -> cooldown -> (optionally) wait for the arcs to go RED again
before re-arming. ``update`` is pure: it takes the current state, the latest AlignmentState, a
monotonic ``now``, and the config, and returns the next state plus a one-shot ``should_fire``.
No cv2, no time calls -- the caller injects ``now`` (so it is fully unit-testable).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from arm101_hand.config.system_camera_config import AutoTriggerConfig

from .arc_detector import AlignmentState

WAIT_GREEN = "WAIT_GREEN"
STABILIZING = "STABILIZING"
COOLDOWN = "COOLDOWN"
WAIT_RED = "WAIT_RED"


@dataclass(frozen=True)
class AutoTriggerState:
    phase: str = WAIT_GREEN
    green_since: float | None = None
    fired_at: float | None = None


def arm() -> AutoTriggerState:
    """Fresh state, ready to watch for green (called when the operator enters AUTO mode)."""
    return AutoTriggerState()


def update(
    state: AutoTriggerState,
    alignment: AlignmentState,
    now: float,
    cfg: AutoTriggerConfig,
) -> tuple[AutoTriggerState, bool]:
    """Advance the lifecycle; return (next_state, should_fire). ``should_fire`` is True one tick."""
    if state.phase == WAIT_GREEN:
        if alignment.ready:
            return replace(state, phase=STABILIZING, green_since=now), False
        return state, False

    if state.phase == STABILIZING:
        if not alignment.ready:
            return replace(state, phase=WAIT_GREEN, green_since=None), False
        assert state.green_since is not None
        if now - state.green_since >= cfg.stable_seconds:
            return replace(state, phase=COOLDOWN, fired_at=now), True
        return state, False

    if state.phase == COOLDOWN:
        assert state.fired_at is not None
        if now - state.fired_at >= cfg.cooldown_seconds:
            nxt = WAIT_RED if cfg.require_red_between else WAIT_GREEN
            return replace(state, phase=nxt, green_since=None, fired_at=None), False
        return state, False

    # WAIT_RED: re-arm once the arcs go red again (operator repositioned for the next patient).
    if alignment.left == "RED" or alignment.right == "RED":
        return replace(state, phase=WAIT_GREEN), False
    return state, False
