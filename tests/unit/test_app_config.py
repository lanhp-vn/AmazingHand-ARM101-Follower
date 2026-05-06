"""Schema tests for ``arm101_hand.config.app_config``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from arm101_hand.config import AppConfig, load_app_config

REPO_ROOT = Path(__file__).resolve().parents[2]
SEEDED_PATH = REPO_ROOT / "data" / "app_config.yaml"


def test_defaults_match_seeded_yaml() -> None:
    """Per ``02-code-style-python.md`` §6: pydantic defaults must match the YAML."""
    cfg_from_defaults = AppConfig()
    cfg_from_yaml = load_app_config(SEEDED_PATH)
    # Per-section comparison gives clearer failures than a top-level dict diff.
    for section in ("hand", "arm", "window", "safety"):
        assert getattr(cfg_from_defaults, section) == getattr(cfg_from_yaml, section), (
            f"section {section!r}: defaults diverge from seeded YAML"
        )


def test_seeded_yaml_loads_clean() -> None:
    """The seeded YAML must round-trip without warnings."""
    cfg = load_app_config(SEEDED_PATH)
    assert cfg.schema_version == 1, "schema_version of seeded YAML"
    assert cfg.hand.port == "COM18", "hand port matches BOM"
    assert cfg.arm.port == "COM20", "arm port matches BOM"
    assert cfg.safety.safe_park.enabled is True, "safe-park enabled by default"


# | bad_payload | description                                  |
@pytest.mark.parametrize(
    "bad_payload,desc",
    [
        ({"hand": {"baudrate": 100}}, "baudrate below ge=9600 rejected"),
        ({"hand": {"timeout": 99.0}}, "timeout above 5.0 rejected"),
        ({"hand": {"default_speed": 0}}, "hand default_speed below 1 rejected"),
        ({"hand": {"default_speed": 6}}, "hand default_speed above 5 rejected"),
        ({"window": {"active_tab": "elbow"}}, "active_tab outside Literal rejected"),
        ({"safety": {"poll_rate_hz": 0.0}}, "poll_rate_hz must be > 0"),
        ({"safety": {"safe_park": {"park_velocity_hand": 6}}}, "hand park_velocity > 5 rejected"),
        ({"safety": {"safe_park": {"arrival_tolerance_deg": 0.0}}}, "tolerance must be > 0"),
        ({"hand": {"unknown_key": 1}}, "extra=forbid rejects unknown keys"),
    ],
)
def test_invalid_payloads_rejected(bad_payload: dict[str, object], desc: str) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(bad_payload)


def test_validation_error_message_contains_offending_field(tmp_path: Path) -> None:
    """The error path should make it clear which field failed (per §6 fail-fast)."""
    bad = {"hand": {"port": "COM18", "baudrate": 100}}
    bad_yaml = tmp_path / "bad_app.yaml"
    bad_yaml.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValidationError) as ei:
        load_app_config(bad_yaml)
    # Pydantic v2 lists field path in the error string; we just check the
    # field name appears so the user can find the line.
    assert "baudrate" in str(ei.value), f"error string mentions baudrate; got: {ei.value!s}"
