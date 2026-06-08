"""Tests for the AmazingHand calibration schema (v3 -- measurement-only)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import DofLimits, HandCalibration
from arm101_hand.config.calibration import load_hand_calibration


def _valid_finger() -> dict:
    return {
        "servo_1": {"middle_pos": 0},
        "servo_2": {"middle_pos": 0},
        "limits": {"base_min": -30, "base_max": 110, "side_min": -40, "side_max": 40},
    }


def _valid_doc() -> dict:
    return {
        "schema_version": 3,
        "fingers": {
            "index": _valid_finger(),
            "middle": _valid_finger(),
            "ring": _valid_finger(),
            "thumb": _valid_finger(),
        },
    }


def test_valid_doc_parses():
    cal = HandCalibration.model_validate(_valid_doc())
    assert cal.schema_version == 3, "accepts a well-formed v3 document"
    assert cal.fingers["thumb"].limits.side_max == 40, "exposes nested DOF limits"


# | mutation                              | desc                                   |
@pytest.mark.parametrize(
    "mutate,desc",
    [
        (lambda d: d.pop("schema_version"), "rejects doc without schema_version"),
        (lambda d: d.__setitem__("schema_version", 2), "rejects schema_version < 3"),
        (lambda d: d["fingers"]["index"].pop("limits"), "rejects a finger missing limits"),
        (lambda d: d["fingers"]["index"]["limits"].__setitem__("extra", 1), "rejects unknown limit key"),
        (lambda d: d.__setitem__("com_port", "COM1"), "rejects extra com_port field"),
        (lambda d: d.__setitem__("speed", 4), "rejects extra speed field"),
    ],
)
def test_rejects_bad_docs(mutate, desc):
    doc = _valid_doc()
    mutate(doc)
    with pytest.raises(ValidationError):
        HandCalibration.model_validate(doc)


# | base_min | base_max | side_min | side_max | desc                          |
@pytest.mark.parametrize(
    "bmin,bmax,smin,smax,desc",
    [
        (110, -30, -40, 40, "rejects base_min >= base_max"),
        (-30, 110, 40, -40, "rejects side_min >= side_max"),
        (0, 0, -40, 40, "rejects base_min == base_max (strict <)"),
    ],
)
def test_limit_ordering_validated(bmin, bmax, smin, smax, desc):
    with pytest.raises(ValidationError):
        DofLimits(base_min=bmin, base_max=bmax, side_min=smin, side_max=smax)


def test_limits_by_finger_lookup():
    cal = HandCalibration.model_validate(_valid_doc())
    by_finger = cal.limits_by_finger()
    assert set(by_finger) == {"index", "middle", "ring", "thumb"}, "one entry per finger"
    assert by_finger["index"].base_max == 110, "returns the DofLimits object"


def test_middle_pos_by_id_uses_canon_table():
    """middle_pos_by_id() maps IDs 1-8 via FINGER_SERVO_IDS, not stored id fields."""
    cal = HandCalibration.model_validate(
        {
            "schema_version": 3,
            "fingers": {
                "index": {
                    "servo_1": {"middle_pos": 30},
                    "servo_2": {"middle_pos": -2},
                    "limits": {"base_min": -30, "base_max": 110, "side_min": -40, "side_max": 40},
                },
                "middle": {
                    "servo_1": {"middle_pos": -32},
                    "servo_2": {"middle_pos": 20},
                    "limits": {"base_min": -30, "base_max": 110, "side_min": -40, "side_max": 40},
                },
                "ring": {
                    "servo_1": {"middle_pos": 22},
                    "servo_2": {"middle_pos": 0},
                    "limits": {"base_min": -30, "base_max": 110, "side_min": -40, "side_max": 40},
                },
                "thumb": {
                    "servo_1": {"middle_pos": 0},
                    "servo_2": {"middle_pos": -12},
                    "limits": {"base_min": -30, "base_max": 110, "side_min": -40, "side_max": 40},
                },
            },
        }
    )
    by_id = cal.middle_pos_by_id()
    assert sorted(by_id.keys()) == list(range(1, 9)), "IDs 1..8 all present"
    assert by_id[1] == 30.0  # index servo_1
    assert by_id[2] == -2.0  # index servo_2
    assert by_id[3] == -32.0  # middle servo_1
    assert by_id[4] == 20.0  # middle servo_2
    assert by_id[5] == 22.0  # ring servo_1
    assert by_id[6] == 0.0  # ring servo_2
    assert by_id[7] == 0.0  # thumb servo_1
    assert by_id[8] == -12.0  # thumb servo_2


def test_loads_canonical_yaml():
    # The committed YAML must satisfy the v3 schema.
    path = Path("scripts/calibration/amazing_hand/hand_calib_values.yaml")
    cal = load_hand_calibration(path)
    assert cal.schema_version == 3, "committed YAML is v3"
    assert len(cal.fingers) == 4, "committed YAML has all four fingers"
