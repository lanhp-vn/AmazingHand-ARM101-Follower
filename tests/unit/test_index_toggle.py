from arm101_hand.hand.index_toggle import (
    TOGGLE_DELTA_DEFAULT,
    TOGGLE_DELTA_MAX,
    TOGGLE_DELTA_MIN,
    ToggleState,
    apply_action,
    in_base,
    key_to_action,
    target_base,
)

# Index calibrated window (hand_calib_values.yaml): base_min=-20, base_max=70.
BASE_MIN, BASE_MAX = -20, 70
# Where 'grab' settles the index (decoded from pose [72, 6]).
GRAB_OUT_BASE, GRAB_SIDE = 33, -39


def test_key_to_action_map():
    assert key_to_action(" ") == "toggle"
    assert key_to_action("[") == "delta-"
    assert key_to_action("]") == "delta+"
    assert key_to_action("q") == "quit"
    assert key_to_action("z") is None


def test_default_state_is_out_with_default_delta():
    state = ToggleState(out_base=GRAB_OUT_BASE, side=GRAB_SIDE)
    assert state.pressed is False
    assert state.delta == TOGGLE_DELTA_DEFAULT


def test_toggle_flips_pressed():
    state = ToggleState(out_base=GRAB_OUT_BASE, side=GRAB_SIDE)
    state = apply_action(state, "toggle")
    assert state.pressed is True
    state = apply_action(state, "toggle")
    assert state.pressed is False


def test_in_base_is_out_plus_delta():
    state = ToggleState(out_base=33, side=GRAB_SIDE, delta=20)
    assert in_base(state, BASE_MIN, BASE_MAX) == 53


def test_in_base_clamps_to_base_max():
    state = ToggleState(out_base=33, side=GRAB_SIDE, delta=40)  # 33+40=73 > 70
    assert in_base(state, BASE_MIN, BASE_MAX) == BASE_MAX


def test_target_base_selects_in_when_pressed_out_otherwise():
    out = ToggleState(out_base=33, side=GRAB_SIDE, delta=20, pressed=False)
    pressed = ToggleState(out_base=33, side=GRAB_SIDE, delta=20, pressed=True)
    assert target_base(out, BASE_MIN, BASE_MAX) == 33
    assert target_base(pressed, BASE_MIN, BASE_MAX) == 53


def test_delta_grows_and_shrinks_within_bounds():
    state = ToggleState(out_base=33, side=GRAB_SIDE, delta=20)
    state = apply_action(state, "delta+")
    assert state.delta == 21
    state = apply_action(state, "delta-")
    assert state.delta == 20


def test_delta_clamps_to_bounds():
    lo = ToggleState(out_base=33, side=GRAB_SIDE, delta=TOGGLE_DELTA_MIN)
    assert apply_action(lo, "delta-").delta == TOGGLE_DELTA_MIN
    hi = ToggleState(out_base=33, side=GRAB_SIDE, delta=TOGGLE_DELTA_MAX)
    assert apply_action(hi, "delta+").delta == TOGGLE_DELTA_MAX


def test_delta_change_does_not_move_pressed_state():
    state = ToggleState(out_base=33, side=GRAB_SIDE, delta=20, pressed=True)
    state = apply_action(state, "delta+")
    assert state.pressed is True  # delta keys never toggle the finger


def test_quit_and_unknown_are_state_noops():
    state = ToggleState(out_base=33, side=GRAB_SIDE)
    assert apply_action(state, "quit") == state
    assert apply_action(state, "nonsense") == state
