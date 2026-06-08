"""Round-trip tests for hand calibration (v3) and hand config save/load."""

from __future__ import annotations

from pathlib import Path

from arm101_hand.config import (
    HandCalibration,
    load_hand_calibration,
    load_hand_config,
    save_hand_calibration,
    save_hand_config,
)
from arm101_hand.config.calibration import DofLimits, FingerCalibration, ServoCalibration
from arm101_hand.config.hand_config import HandConfig, HandPose

SEED = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "calibration"
    / "amazing_hand"
    / "hand_calib_values.yaml"
)


def _v3_finger() -> FingerCalibration:
    return FingerCalibration(
        servo_1=ServoCalibration(middle_pos=10),
        servo_2=ServoCalibration(middle_pos=-5),
        limits=DofLimits(base_min=-20, base_max=70, side_min=-40, side_max=35),
    )


def test_calibration_round_trip(tmp_path: Path) -> None:
    """v3 HandCalibration saves and reloads cleanly (no id / connection fields)."""
    cfg = HandCalibration(
        schema_version=3,
        fingers={
            "index": _v3_finger(),
            "middle": _v3_finger(),
            "ring": _v3_finger(),
            "thumb": _v3_finger(),
        },
    )
    out = tmp_path / "calib.yaml"
    save_hand_calibration(out, cfg)
    reloaded = load_hand_calibration(out)
    assert reloaded.model_dump() == cfg.model_dump()


def test_seed_yaml_is_v3(tmp_path: Path) -> None:
    """The committed calib YAML must satisfy the v3 schema (no id/connection keys)."""
    cfg = load_hand_calibration(SEED)
    assert cfg.schema_version == 3, "committed YAML is v3"
    assert len(cfg.fingers) == 4, "all four fingers present"
    # No id/connection/speed fields on v3 HandCalibration.
    assert not hasattr(cfg, "com_port")
    assert not hasattr(cfg, "speed")


def test_middle_pos_by_id_uses_canon_table() -> None:
    """middle_pos_by_id() derives IDs from FINGER_SERVO_IDS, not stored id fields."""
    cfg = HandCalibration(
        schema_version=3,
        fingers={
            "index": FingerCalibration(
                servo_1=ServoCalibration(middle_pos=30),
                servo_2=ServoCalibration(middle_pos=-2),
                limits=DofLimits(base_min=-20, base_max=70, side_min=-40, side_max=35),
            ),
            "middle": FingerCalibration(
                servo_1=ServoCalibration(middle_pos=-32),
                servo_2=ServoCalibration(middle_pos=20),
                limits=DofLimits(base_min=-35, base_max=65, side_min=-20, side_max=15),
            ),
            "ring": FingerCalibration(
                servo_1=ServoCalibration(middle_pos=22),
                servo_2=ServoCalibration(middle_pos=0),
                limits=DofLimits(base_min=-35, base_max=65, side_min=-25, side_max=20),
            ),
            "thumb": FingerCalibration(
                servo_1=ServoCalibration(middle_pos=0),
                servo_2=ServoCalibration(middle_pos=-12),
                limits=DofLimits(base_min=-40, base_max=100, side_min=-55, side_max=50),
            ),
        },
    )
    by_id = cfg.middle_pos_by_id()
    assert sorted(by_id.keys()) == list(range(1, 9)), "IDs 1-8 all present"
    assert by_id[1] == 30.0
    assert by_id[2] == -2.0
    assert by_id[3] == -32.0
    assert by_id[4] == 20.0
    assert by_id[5] == 22.0
    assert by_id[6] == 0.0
    assert by_id[7] == 0.0
    assert by_id[8] == -12.0


def test_hand_config_round_trip(tmp_path: Path) -> None:
    """HandConfig (with poses) saves and reloads cleanly."""
    cfg = HandConfig(
        poses={"grab": HandPose(index=[70, 2], middle=[59, -19], ring=[64, -14], thumb=[59, -59])}
    )
    out = tmp_path / "hand_config.yaml"
    save_hand_config(out, cfg)
    reloaded = load_hand_config(out)
    assert reloaded.poses["grab"].by_finger() == {
        "index": [70, 2],
        "middle": [59, -19],
        "ring": [64, -14],
        "thumb": [59, -59],
    }
