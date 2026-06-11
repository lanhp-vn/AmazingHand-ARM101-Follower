import hashlib
import json

from arm101_hand.fundus_analysis.grader import GradeResult
from arm101_hand.fundus_analysis.sidecar import sidecar_path, weights_sha8, write_sidecar


def _result(name: str = "img01.JPG") -> GradeResult:
    return GradeResult(
        source_image=name,
        grade=2,
        label="Moderate",
        confidence="MEDIUM",
        probabilities={"No DR": 0.04, "Mild": 0.18, "Moderate": 0.71, "Severe": 0.05, "Proliferative": 0.02},
        crop={"method": "circle", "box": [0, 0, 10, 10], "fallback": False},
        model={"checkpoint": "w.safetensors", "sha256_8": "abc12345", "arch": "vit_large_patch16_224"},
        preprocess_version="1",
        graded_at_utc="2026-06-11T00:00:00Z",
    )


def test_sidecar_path_uses_source_stem(tmp_path):
    assert sidecar_path(tmp_path, "20260611_IM0115EY.JPG") == tmp_path / "20260611_IM0115EY.dr.json"


def test_write_sidecar_creates_dir_and_round_trips(tmp_path):
    out = tmp_path / "fundus_analysis"
    res = _result("img01.JPG")
    path = write_sidecar(res, out)
    assert path == out / "img01.dr.json"
    assert json.loads(path.read_text()) == res.to_dict()


def test_weights_sha8_matches_hashlib(tmp_path):
    f = tmp_path / "w.bin"
    f.write_bytes(b"hello world")
    assert weights_sha8(f) == hashlib.sha256(b"hello world").hexdigest()[:8]
