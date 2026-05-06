"""Schema tests for ``arm101_hand.config.hand_poses``."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import HandPoseConfig, load_hand_poses
from arm101_hand.config.hand_poses import POSITIONS_LEN

REPO_ROOT = Path(__file__).resolve().parents[2]
SEEDED_PATH = REPO_ROOT / "data" / "hand_config.yaml"


def test_seeded_yaml_loads_clean() -> None:
    cfg = load_hand_poses(SEEDED_PATH)
    assert cfg.schema_version == 1, "schema_version of seeded YAML"
    assert "middle" in cfg.poses, "seeded `middle` pose present (default safe-park target)"
    assert cfg.poses["middle"].positions == [0] * POSITIONS_LEN, (
        "seeded `middle` is all-zeros (calibrated middle for every servo)"
    )


def test_empty_poses_and_sequences_are_valid() -> None:
    cfg = HandPoseConfig.model_validate({"schema_version": 1, "poses": {}, "sequences": {}})
    assert cfg.poses == {}, "empty poses dict accepted"
    assert cfg.sequences == {}, "empty sequences dict accepted"


# | positions | description                                          |
@pytest.mark.parametrize(
    "positions,desc",
    [
        ([0] * 7, "7 entries rejected (must be exactly 8)"),
        ([0] * 9, "9 entries rejected (must be exactly 8)"),
        ([], "empty list rejected"),
    ],
)
def test_position_length_must_be_eight(positions: list[int], desc: str) -> None:
    with pytest.raises(ValidationError):
        HandPoseConfig.model_validate({"poses": {"x": {"positions": positions}}})


def test_position_must_be_int_typed() -> None:
    """Floats coerce to int via pydantic strict-ish mode; obvious garbage is rejected."""
    with pytest.raises(ValidationError):
        HandPoseConfig.model_validate({"poses": {"x": {"positions": ["a"] * 8}}})


def test_extra_fields_rejected_on_pose() -> None:
    with pytest.raises(ValidationError):
        HandPoseConfig.model_validate({"poses": {"x": {"positions": [0] * 8, "garbage": 1}}})


# | step | valid | description                                              |
@pytest.mark.parametrize(
    "step,valid,desc",
    [
        ("middle:3,3,3,3,3,3,3,3|2.0s", True, "well-formed pose step accepted"),
        ("SLEEP:0.5s", True, "well-formed sleep step accepted"),
        ("middle:3,3,3,3,3,3,3,3", False, "pose step missing |delay rejected"),
        ("just-a-string", False, "freeform string rejected"),
    ],
)
def test_sequence_step_format(step: str, valid: bool, desc: str) -> None:
    payload = {"sequences": {"demo": {"steps": [step]}}}
    if valid:
        HandPoseConfig.model_validate(payload)  # no raise
    else:
        with pytest.raises(ValidationError):
            HandPoseConfig.model_validate(payload)
