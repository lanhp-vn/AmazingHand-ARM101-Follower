"""Tests for the seeded ``src/arm101_hand/data/hand_config.yaml``."""

from __future__ import annotations

from pathlib import Path

from arm101_hand.config import load_hand_config

_HAND_CONFIG_PATH = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "hand_config.yaml"


def test_seeded_yaml_loads_clean() -> None:
    cfg = load_hand_config(_HAND_CONFIG_PATH)
    assert cfg.schema_version == 1, "schema_version of seeded YAML"
    assert cfg.connection.port == "COM18", "connection.port matches YAML"
    assert "middle" in cfg.poses, "seeded 'middle' pose is present"
    assert cfg.poses["middle"].by_finger() == {
        "index": [0, 0],
        "middle": [0, 0],
        "ring": [0, 0],
        "thumb": [0, 0],
    }, "seeded 'middle' is all [0, 0] per finger"
