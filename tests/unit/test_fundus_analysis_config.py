import pytest
from pydantic import ValidationError

from arm101_hand.config.fundus_analysis_config import (
    DR_LABELS,
    FundusAnalysisConfig,
    load_fundus_analysis_config,
)


def test_default_config_loads_from_yaml():
    cfg = load_fundus_analysis_config()
    assert cfg.input_size == 224
    assert cfg.num_classes == 5
    assert cfg.confidence.high >= cfg.confidence.medium
    assert cfg.weights_filename.endswith(".safetensors")


def test_label_map_is_canonical_aptos_order():
    assert DR_LABELS == {
        0: "No DR",
        1: "Mild",
        2: "Moderate",
        3: "Severe",
        4: "Proliferative",
    }


def test_confidence_bands_rejects_inverted_thresholds():
    with pytest.raises(ValidationError):
        FundusAnalysisConfig(confidence={"high": 0.5, "medium": 0.8})


def test_confidence_thresholds_in_unit_interval():
    with pytest.raises(ValidationError):
        FundusAnalysisConfig(confidence={"high": 1.5, "medium": 0.8})
