"""Diabetic-retinopathy grading from fundus photos (device layer).

Local, offline inference only — no network calls. Research/educational use;
not a medical device.
"""

from arm101_hand.config.fundus_analysis_config import DR_LABELS
from arm101_hand.fundus_analysis.grader import DRGrader, GradeResult
from arm101_hand.fundus_analysis.preprocess import (
    CropInfo,
    build_eval_transform,
    circle_crop,
    confidence_band,
)

__all__ = [
    "DRGrader",
    "GradeResult",
    "DR_LABELS",
    "CropInfo",
    "build_eval_transform",
    "circle_crop",
    "confidence_band",
]
