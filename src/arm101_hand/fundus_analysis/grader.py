"""DRGrader: load-once diabetic-retinopathy grader. Reusable by Phase 2.

Local/offline only — no network calls anywhere in this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

from arm101_hand.config.fundus_analysis_config import DR_LABELS, FundusAnalysisConfig
from arm101_hand.fundus_analysis.model import build_model, load_weights
from arm101_hand.fundus_analysis.preprocess import (
    CropInfo,
    build_eval_transform,
    circle_crop,
    confidence_band,
)

_DISCLAIMER = "Research/educational use only. Not a medical device; not for clinical diagnosis."


@dataclass(frozen=True)
class GradeResult:
    """One image's grading outcome (serializes straight to the JSON sidecar)."""

    source_image: str
    grade: int
    label: str
    confidence: str
    probabilities: dict[str, float]
    crop: dict[str, object]
    model: dict[str, str]
    preprocess_version: str
    graded_at_utc: str
    disclaimer: str = _DISCLAIMER

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class DRGrader:
    """Construct the model + transform once; grade many images."""

    def __init__(
        self,
        config: FundusAnalysisConfig,
        *,
        model: torch.nn.Module | None = None,
        weights_path: Path | None = None,
        model_sha8: str = "unknown",
    ) -> None:
        self._cfg = config
        self._transform = build_eval_transform(config.input_size)
        self._model_sha8 = model_sha8
        if model is not None:
            self._model = model
        else:
            self._model = build_model(config.model_arch, config.num_classes)
            wp = weights_path or (Path(config.models_dir) / config.weights_filename)
            load_weights(self._model, wp)
        self._model.eval()

    def grade_array(self, img_bgr: np.ndarray, *, source_name: str) -> GradeResult:
        cropped, info = circle_crop(
            img_bgr,
            background_threshold=self._cfg.crop.background_threshold,
            margin_frac=self._cfg.crop.margin_frac,
            min_disc_area_frac=self._cfg.crop.min_disc_area_frac,
        )
        rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
        tensor = self._transform(Image.fromarray(rgb)).unsqueeze(0)
        with torch.inference_mode():
            logits = self._model(tensor)
            probs = torch.softmax(logits, dim=1)[0]
        grade = int(torch.argmax(probs).item())
        top_prob = float(probs[grade].item())
        return GradeResult(
            source_image=source_name,
            grade=grade,
            label=DR_LABELS[grade],
            confidence=confidence_band(
                top_prob, high=self._cfg.confidence.high, medium=self._cfg.confidence.medium
            ),
            probabilities={
                DR_LABELS[i]: round(float(probs[i].item()), 6) for i in range(len(DR_LABELS))
            },
            crop=self._crop_to_dict(info),
            model={
                "checkpoint": self._cfg.weights_filename,
                "sha256_8": self._model_sha8,
                "arch": self._cfg.model_arch,
            },
            preprocess_version=self._cfg.preprocess_version,
            graded_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def grade_path(self, image_path: Path) -> GradeResult:
        img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Could not read image: {image_path}")
        return self.grade_array(img, source_name=image_path.name)

    @staticmethod
    def _crop_to_dict(info: CropInfo) -> dict[str, object]:
        return {"method": info.method, "box": list(info.box), "fallback": info.fallback}
