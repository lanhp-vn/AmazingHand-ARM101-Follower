# tests/unit/test_index_trigger.py
from arm101_hand.hand.index_toggle import TOGGLE_DELTA_DEFAULT, TOGGLE_DELTA_MAX, TOGGLE_DELTA_MIN
from arm101_hand.hand.index_trigger import TriggerState, apply_action, key_to_action, press_base

BASE_MIN, BASE_MAX = -20, 70


def test_key_map():
    assert key_to_action(" ") == "fire"
    assert key_to_action("[") == "delta-"
    assert key_to_action("]") == "delta+"
    assert key_to_action("q") == "quit"
    assert key_to_action("z") is None


def test_default_delta():
    assert TriggerState(out_base=33, side=-39).delta == TOGGLE_DELTA_DEFAULT


def test_press_base_is_out_plus_delta_clamped():
    assert press_base(TriggerState(out_base=33, side=-39, delta=20), BASE_MIN, BASE_MAX) == 53
    assert press_base(TriggerState(out_base=33, side=-39, delta=40), BASE_MIN, BASE_MAX) == BASE_MAX  # 73>70


def test_delta_grows_shrinks_clamps():
    s = TriggerState(out_base=33, side=-39, delta=20)
    assert apply_action(s, "delta+").delta == 21
    assert apply_action(s, "delta-").delta == 19
    assert apply_action(TriggerState(33, -39, TOGGLE_DELTA_MAX), "delta+").delta == TOGGLE_DELTA_MAX
    assert apply_action(TriggerState(33, -39, TOGGLE_DELTA_MIN), "delta-").delta == TOGGLE_DELTA_MIN


def test_fire_and_quit_are_state_noops():
    s = TriggerState(out_base=33, side=-39, delta=20)
    assert apply_action(s, "fire") == s
    assert apply_action(s, "quit") == s
    assert apply_action(s, "nonsense") == s
