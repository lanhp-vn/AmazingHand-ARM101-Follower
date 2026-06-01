"""Tests for the AmazingHand calibration schema (v2 with DOF limits)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from arm101_hand.config import DofLimits, HandCalibration
from arm101_hand.config.calibration import load_hand_calibration


def _valid_finger(sid1: int, sid2: int) -> dict:
    return {
        "servo_1": {"id": sid1, "middle_pos": 0},
        "servo_2": {"id": sid2, "middle_pos": 0},
        "limits": {"base_min": -30, "base_max": 110, "side_min": -40, "side_max": 40},
    }


def _valid_doc() -> dict:
    return {
        "schema_version": 2,
        "com_port": "COM18",
        "baudrate": 1000000,
        "timeout": 0.5,
        "speed": 6,
        "fingers": {
            "index": _valid_finger(1, 2),
            "middle": _valid_finger(3, 4),
            "ring": _valid_finger(5, 6),
            "thumb": _valid_finger(7, 8),
        },
    }


def test_valid_doc_parses():
    cal = HandCalibration.model_validate(_valid_doc())
    assert cal.schema_version == 2, "accepts a well-formed v2 document"
    assert cal.fingers["thumb"].limits.side_max == 40, "exposes nested DOF limits"


# | mutation                              | desc                                   |
@pytest.mark.parametrize("mutate,desc", [
    (lambda d: d.pop("schema_version"),          "rejects v1 doc (no schema_version)"),
    (lambda d: d.__setitem__("schema_version", 1), "rejects schema_version < 2"),
    (lambda d: d["fingers"]["index"].pop("limits"), "rejects a finger missing limits"),
    (lambda d: d["fingers"]["index"]["limits"].__setitem__("extra", 1), "rejects unknown limit key"),
])
def test_rejects_bad_docs(mutate, desc):
    doc = _valid_doc()
    mutate(doc)
    with pytest.raises(ValidationError):
        HandCalibration.model_validate(doc)


# | base_min | base_max | side_min | side_max | desc                          |
@pytest.mark.parametrize("bmin,bmax,smin,smax,desc", [
    (110, -30, -40, 40, "rejects base_min >= base_max"),
    (-30, 110, 40, -40, "rejects side_min >= side_max"),
])
def test_limit_ordering_validated(bmin, bmax, smin, smax, desc):
    with pytest.raises(ValidationError):
        DofLimits(base_min=bmin, base_max=bmax, side_min=smin, side_max=smax)


def test_limits_by_finger_lookup():
    cal = HandCalibration.model_validate(_valid_doc())
    by_finger = cal.limits_by_finger()
    assert set(by_finger) == {"index", "middle", "ring", "thumb"}, "one entry per finger"
    assert by_finger["index"].base_max == 110, "returns the DofLimits object"


def test_loads_canonical_yaml(tmp_path):
    # The committed YAML must satisfy the v2 schema. Load it through the public loader.
    from pathlib import Path
    path = Path("scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml")
    cal = load_hand_calibration(path)
    assert cal.schema_version == 2, "committed YAML is v2"
    assert len(cal.fingers) == 4, "committed YAML has all four fingers"
