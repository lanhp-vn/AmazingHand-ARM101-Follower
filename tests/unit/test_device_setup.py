# tests/unit/test_device_setup.py
from dataclasses import dataclass

from arm101_hand.scripts import device_setup


def test_calib_path_points_at_so_arm101_json():
    assert device_setup.CALIB_PATH.name == "so101_follower.json"
    assert device_setup.CALIB_PATH.parent.name == "so_arm101"


def test_arm_config_path_is_package_data():
    assert device_setup.ARM_CONFIG_PATH.parts[-3:] == ("arm101_hand", "data", "arm_config.yaml")


def test_exports_builders():
    for name in ("build_raw_bus", "build_follower", "load_arm_app_config", "FOLLOWER_ID"):
        assert hasattr(device_setup, name)


@dataclass
class _Cal:
    range_min: int
    range_max: int


class _FakeBus:
    """Records sync_write calls; a Goal_Position write instantly 'moves' present pos to goal."""

    def __init__(self, present: dict[str, int]):
        self.present = dict(present)
        self.writes: list[tuple[str, dict, object]] = []

    def sync_write(self, reg, values, normalize=True):
        self.writes.append((reg, dict(values), normalize))
        if reg == "Goal_Position":
            self.present.update(values)

    def sync_read(self, reg, normalize=True):
        assert reg == "Present_Position"
        return dict(self.present)

    def disable_torque(self):
        self.writes.append(("disable_torque", {}, None))

    def enable_torque(self):
        self.writes.append(("enable_torque", {}, None))


class _FakeFollower:
    def __init__(self, calibration, bus, is_connected=True):
        self.calibration = calibration
        self.bus = bus
        self.is_connected = is_connected


def _full_calib():
    return {j: _Cal(0, 4095) for j in device_setup.ARM_JOINTS}


def test_drive_arm_joints_writes_only_subset_and_converts_degrees():
    bus = _FakeBus(dict.fromkeys(device_setup.ARM_JOINTS, 0))
    follower = _FakeFollower(_full_calib(), bus)
    device_setup.drive_arm_joints(
        follower, {"shoulder_pan": 90.0}, vel=600, tolerance=25, timeout_s=1.0, poll_s=0.0
    )
    goal_writes = [w for w in bus.writes if w[0] == "Goal_Position"]
    assert len(goal_writes) == 1
    _reg, values, normalize = goal_writes[0]
    assert normalize is False
    assert set(values) == {"shoulder_pan"}, "only the requested joint is commanded"
    # 90 deg is a quarter turn. Steps/deg = (4096-1)/360 = 11.375; mid of 0..4095 = 2047.5.
    # 2047.5 + 90*11.375 = 3071.25 -> rounds to 3071. Derived by hand, NOT from the impl.
    assert values["shoulder_pan"] == 3071
    vel_writes = [w for w in bus.writes if w[0] == "Goal_Velocity"]
    assert vel_writes and set(vel_writes[0][1]) == {"shoulder_pan"}, "velocity scoped to subset"


def test_safe_enable_torque_seeds_present_and_caps_velocity_before_enable():
    # Stale present pose the joints must HOLD (not snap away from) when torque turns on.
    present = dict.fromkeys(device_setup.ARM_JOINTS, 1234)
    bus = _FakeBus(present)
    follower = _FakeFollower(_full_calib(), bus)
    device_setup.safe_enable_torque(follower, vel=600)

    kinds = [w[0] for w in bus.writes]
    enable_idx = kinds.index("enable_torque")
    gp_idx = next(i for i, w in enumerate(bus.writes) if w[0] == "Goal_Position")
    gv_idx = next(i for i, w in enumerate(bus.writes) if w[0] == "Goal_Velocity")
    # Both the goal seed and the velocity cap must land BEFORE torque is enabled,
    # else enabling torque snaps to a stale goal at unlimited speed (the power-up lunge).
    assert gp_idx < enable_idx, "Goal_Position must be seeded before enable_torque"
    assert gv_idx < enable_idx, "Goal_Velocity cap must be set before enable_torque"

    _reg, gp_vals, gp_norm = bus.writes[gp_idx]
    assert gp_vals == present, "goal seeded to the present pose (no-jump hold)"
    assert gp_norm is False, "raw encoder steps (works in any norm mode)"
    _reg, gv_vals, _norm = bus.writes[gv_idx]
    assert gv_vals == dict.fromkeys(device_setup.ARM_JOINTS, 600), "velocity capped on all joints"


def test_drive_arm_joints_empty_is_noop():
    bus = _FakeBus({})
    follower = _FakeFollower({}, bus)
    device_setup.drive_arm_joints(follower, {}, vel=600)
    assert bus.writes == []


def test_drive_home_commands_all_five_joints():
    bus = _FakeBus(dict.fromkeys(device_setup.ARM_JOINTS, 0))
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)
    device_setup._drive_home(follower, home, vel=600, timeout_s=1.0, poll_s=0.0)
    goal = [w for w in bus.writes if w[0] == "Goal_Position"][0]
    assert set(goal[1]) == set(device_setup.ARM_JOINTS), "_drive_home still drives all joints"


def test_drive_arm_joints_times_out_without_convergence(capsys):
    class _StuckBus(_FakeBus):
        # Never moves present toward goal -> the wait loop must time out, not hang.
        def sync_write(self, reg, values, normalize=True):
            self.writes.append((reg, dict(values), normalize))

    bus = _StuckBus(dict.fromkeys(device_setup.ARM_JOINTS, 0))
    follower = _FakeFollower(_full_calib(), bus)
    device_setup.drive_arm_joints(
        follower, {"shoulder_pan": 90.0}, vel=600, tolerance=1, timeout_s=0.05, poll_s=0.0
    )
    err = capsys.readouterr().err
    assert "not fully reached within timeout" in err, "timeout path prints the note to stderr"


def test_confirm_and_release_runs_home_routine_on_h(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "h")
    bus = _FakeBus(dict.fromkeys(device_setup.ARM_JOINTS, 0))
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)
    called = []
    device_setup.confirm_and_release(
        follower, True, home, 600, offer_home=True, home_routine=lambda: called.append(True)
    )
    assert called == [True], "home_routine ran on 'h'"
    assert not any(w[0] == "Goal_Position" for w in bus.writes), "staged routine replaces default homing"
    assert any(w[0] == "disable_torque" for w in bus.writes), "arm torque always released (IL-4)"


def test_confirm_and_release_default_homes_on_h_without_routine(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "h")
    bus = _FakeBus(dict.fromkeys(device_setup.ARM_JOINTS, 0))
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)
    device_setup.confirm_and_release(follower, True, home, 600, offer_home=True)
    assert any(w[0] == "Goal_Position" for w in bus.writes), "default 'h' path drives home"
    assert any(w[0] == "disable_torque" for w in bus.writes)


def test_confirm_and_release_release_in_place_on_enter(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    bus = _FakeBus(dict.fromkeys(device_setup.ARM_JOINTS, 0))
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)
    called = []
    device_setup.confirm_and_release(
        follower, True, home, 600, offer_home=True, home_routine=lambda: called.append(True)
    )
    assert called == [], "plain Enter does not run the staged reverse"
    assert not any(w[0] == "Goal_Position" for w in bus.writes), "no movement on release-in-place"
    assert any(w[0] == "disable_torque" for w in bus.writes)


def test_confirm_and_release_home_routine_raises_torque_still_off(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "h")
    bus = _FakeBus(dict.fromkeys(device_setup.ARM_JOINTS, 0))
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)

    def _crashing_routine():
        raise RuntimeError("boom")

    device_setup.confirm_and_release(
        follower, True, home, 600, offer_home=True, home_routine=_crashing_routine
    )
    assert any(w[0] == "disable_torque" for w in bus.writes), (
        "torque always off (IL-4) even when home_routine raises"
    )


def test_confirm_and_release_home_routine_skips_on_home(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "h")
    bus = _FakeBus(dict.fromkeys(device_setup.ARM_JOINTS, 0))
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)
    routine_called = []
    on_home_called = []
    device_setup.confirm_and_release(
        follower,
        True,
        home,
        600,
        offer_home=True,
        on_home=lambda: on_home_called.append(True),
        home_routine=lambda: routine_called.append(True),
    )
    assert routine_called == [True], "home_routine ran"
    assert on_home_called == [], "on_home is ignored when home_routine is set"
