import numpy as np
import torch

from arm101_hand.config.fundus_analysis_config import load_fundus_analysis_config
from arm101_hand.fundus_analysis.grader import DRGrader, GradeResult


class _StubModel(torch.nn.Module):
    """Returns fixed logits favouring class 2 (Moderate)."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        n = x.shape[0]
        logits = torch.tensor([[0.1, 0.2, 3.0, 0.1, 0.05]], dtype=torch.float32)
        return logits.repeat(n, 1)


def _grader() -> DRGrader:
    cfg = load_fundus_analysis_config()
    return DRGrader(cfg, model=_StubModel())


def test_grade_array_returns_moderate():
    g = _grader()
    img = np.full((1776, 2368, 3), 130, dtype=np.uint8)
    res = g.grade_array(img, source_name="x.JPG")
    assert isinstance(res, GradeResult)
    assert res.grade == 2
    assert res.label == "Moderate"
    assert set(res.probabilities) == {"No DR", "Mild", "Moderate", "Severe", "Proliferative"}
    assert abs(sum(res.probabilities.values()) - 1.0) < 1e-5
    assert res.confidence in {"HIGH", "MEDIUM", "LOW"}


def test_grade_result_to_dict_has_required_fields_and_disclaimer():
    g = _grader()
    img = np.full((1776, 2368, 3), 130, dtype=np.uint8)
    d = g.grade_array(img, source_name="x.JPG").to_dict()
    for key in (
        "source_image",
        "grade",
        "label",
        "confidence",
        "probabilities",
        "crop",
        "model",
        "preprocess_version",
        "graded_at_utc",
        "disclaimer",
    ):
        assert key in d
    assert "not a medical device" in d["disclaimer"].lower()
    assert d["graded_at_utc"].endswith("Z")
