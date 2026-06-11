"""Pydantic config for DR-grading inference (primitive layer).

Loaded from ``src/arm101_hand/data/fundus_analysis_config.yaml``. Operator-editable;
never written at runtime (IL-5).
"""

from __future__ import annotations

from importlib import resources
from typing import Final

import yaml
from pydantic import BaseModel, Field, model_validator

# Canonical APTOS2019 5-class severity map (ImageFolder alphabetical order).
DR_LABELS: Final[dict[int, str]] = {
    0: "No DR",
    1: "Mild",
    2: "Moderate",
    3: "Severe",
    4: "Proliferative",
}


class ConfidenceBands(BaseModel):
    """Top-probability thresholds for the confidence label."""

    high: float = Field(0.80, ge=0.0, le=1.0)
    medium: float = Field(0.65, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> ConfidenceBands:
        if self.high < self.medium:
            raise ValueError("confidence.high must be >= confidence.medium")
        return self


class CircleCropParams(BaseModel):
    """Parameters for the fundus circle-crop preprocessor."""

    background_threshold: int = Field(12, ge=0, le=255)
    margin_frac: float = Field(0.02, ge=0.0, le=0.25)
    min_disc_area_frac: float = Field(0.05, ge=0.0, le=1.0)


class FundusAnalysisConfig(BaseModel):
    """Top-level DR-grading config."""

    model_arch: str = "vit_large_patch16_224"
    input_size: int = 224
    num_classes: int = 5
    weights_filename: str = "retfound_aptos2019_vitl16.safetensors"
    models_dir: str = "models"
    images_dir: str = "media_outputs/fundus_images"
    output_dir: str = "media_outputs/fundus_analysis"
    preprocess_version: str = "1"
    confidence: ConfidenceBands = ConfidenceBands()
    crop: CircleCropParams = CircleCropParams()


def load_fundus_analysis_config() -> FundusAnalysisConfig:
    """Load the bundled YAML defaults into a validated config object."""
    text = resources.files("arm101_hand.data").joinpath("fundus_analysis_config.yaml").read_text()
    data = yaml.safe_load(text) or {}
    return FundusAnalysisConfig(**data)
