"""Unit tests for the offline arc-sweep replay diagnostic's pure helpers.

The helpers live in scripts/diagnostics/system_camera/arc_sweep_replay.py. scripts/ is not an
importable package here (scripts add src/ to sys.path and import arm101_hand; nothing imports a
script *module*), so we load via a path-based import -- the same pattern as test_arc_debug_sidecar.py.
Safe because the script's top level only defines functions + constants; main() is guarded by __main__.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from arm101_hand.config import load_system_camera_config
from arm101_hand.config.system_camera_config import RoiBox
from arm101_hand.system_camera.calibration import (
    ArcCase,
    SweepResult,
    sweep_red_detection,
    write_calibration_values,
)

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "diagnostics" / "system_camera" / "arc_sweep_replay.py"
)
_DATA = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"

_LEFT = RoiBox(x=120, y=120, w=80, h=240, ref_w=640, ref_h=480)
_RIGHT = RoiBox(x=480, y=120, w=80, h=240, ref_w=640, ref_h=480)


def _load_module():
    spec = importlib.util.spec_from_file_location("arc_sweep_replay", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: the script's frozen @dataclass resolves its string annotations
    # (from __future__ import annotations) via sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_case(case_dir: Path, stem: str, expected: str, color_bgr: tuple[int, int, int]) -> None:
    """Write a <stem>.json sidecar + a 640x480 <stem>_clean.png with both arc boxes filled."""
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    for arc in (_LEFT, _RIGHT):
        img[arc.y : arc.y + arc.h, arc.x : arc.x + arc.w] = color_bgr
    cv2.imwrite(str(case_dir / f"{stem}_clean.png"), img)
    sidecar = {"expected": expected, "left_arc": _LEFT.model_dump(), "right_arc": _RIGHT.model_dump()}
    (case_dir / f"{stem}.json").write_text(json.dumps(sidecar), encoding="utf-8")


def test_load_arc_cases_reads_labelled_and_skips_unlabelled(tmp_path):
    mod = _load_module()
    _write_case(tmp_path, "calib_a", "red", (0, 0, 200))
    _write_case(tmp_path, "calib_b", "clear", (80, 160, 80))
    # an unlabelled sidecar (arc_debug style: only a free note + a clean PNG) -> skipped
    (tmp_path / "arc_c.json").write_text(
        json.dumps(
            {"expected_note": "looks red", "left_arc": _LEFT.model_dump(), "right_arc": _RIGHT.model_dump()}
        ),
        encoding="utf-8",
    )
    cv2.imwrite(str(tmp_path / "arc_c_clean.png"), np.zeros((480, 640, 3), dtype=np.uint8))

    cases = mod.load_arc_cases(tmp_path)
    assert [lc.stem for lc in cases] == ["calib_a", "calib_b"]  # sorted; unlabelled skipped
    assert [lc.case.expected for lc in cases] == ["red", "clear"]
    assert (cases[0].left_arc.x, cases[0].right_arc.x) == (_LEFT.x, _RIGHT.x)


def test_load_arc_cases_skips_missing_clean_png(tmp_path):
    mod = _load_module()
    (tmp_path / "calib_x.json").write_text(
        json.dumps({"expected": "red", "left_arc": _LEFT.model_dump(), "right_arc": _RIGHT.model_dump()}),
        encoding="utf-8",
    )  # no _clean.png written -> skipped
    assert mod.load_arc_cases(tmp_path) == []


def test_load_then_sweep_is_separable(tmp_path):
    mod = _load_module()
    _write_case(tmp_path, "calib_red", "red", (0, 0, 200))
    _write_case(tmp_path, "calib_clear", "clear", (80, 160, 80))
    cases = mod.load_arc_cases(tmp_path)
    res = sweep_red_detection([lc.case for lc in cases], cases[0].left_arc, cases[0].right_arc)
    assert res.separable is True
    report = mod.format_sweep_report(res, cases)
    assert "coverage_threshold" in report
    assert "fitted cases correct" in report


def test_format_sweep_report_marks_excluded_wrong_and_ok():
    mod = _load_module()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cases = [
        mod.LabeledCase("c0", ArcCase(frame=frame, expected="red"), _LEFT, _RIGHT),
        mod.LabeledCase("c1", ArcCase(frame=frame, expected="red"), _LEFT, _RIGHT),
        mod.LabeledCase("c2", ArcCase(frame=frame, expected="clear"), _LEFT, _RIGHT),
    ]
    res = SweepResult(
        a_star_min=134,
        coverage_threshold=0.0096,
        separable=False,
        unsatisfied=[1],
        case_detections=[(True, True), (False, False), (False, False)],
        excluded_transitional=[0],
    )
    report = mod.format_sweep_report(res, cases)
    assert "1/2 fitted cases correct (1 excluded as transitional)" in report
    assert "[ 0] EXCLUDED" in report
    assert "[ 1] WRONG" in report
    assert "[ 2] ok" in report


def test_build_write_kwargs_preserves_screen_roi_and_uses_case_arcs():
    mod = _load_module()
    screen_roi = RoiBox(x=200, y=220, w=410, h=246, ref_w=640, ref_h=480, angle=-1.0)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cases = [mod.LabeledCase("c0", ArcCase(frame=frame, expected="red"), _LEFT, _RIGHT)]
    res = SweepResult(
        a_star_min=134,
        coverage_threshold=0.0096,
        separable=True,
        unsatisfied=[],
        case_detections=[(True, True)],
        excluded_transitional=[],
    )
    kw = mod.build_write_kwargs(res, cases, screen_roi)
    assert kw["screen_roi"] is screen_roi
    assert kw["left_arc"] == _LEFT and kw["right_arc"] == _RIGHT
    assert kw["a_star_min"] == res.a_star_min
    assert kw["coverage_threshold"] == 0.0096


def test_write_kwargs_round_trip_updates_config(tmp_path):
    """End-to-end: load cases -> sweep -> build kwargs -> write to a temp config copy -> reload."""
    mod = _load_module()
    dst = tmp_path / "system_camera_config.yaml"
    dst.write_text(_DATA.read_text(encoding="utf-8"), encoding="utf-8")
    current = load_system_camera_config(dst)

    cdir = tmp_path / "cases"
    cdir.mkdir()
    _write_case(cdir, "calib_red", "red", (0, 0, 200))
    _write_case(cdir, "calib_clear", "clear", (80, 160, 80))
    cases = mod.load_arc_cases(cdir)
    res = sweep_red_detection([lc.case for lc in cases], cases[0].left_arc, cases[0].right_arc)
    assert res.separable is True

    write_calibration_values(dst, **mod.build_write_kwargs(res, cases, current.screen_roi))

    reloaded = load_system_camera_config(dst)
    assert reloaded.auto_trigger.coverage_threshold == res.coverage_threshold
    assert reloaded.auto_trigger.a_star_min == res.a_star_min
    assert (reloaded.auto_trigger.left_arc.x, reloaded.auto_trigger.right_arc.x) == (_LEFT.x, _RIGHT.x)
    assert reloaded.screen_roi == current.screen_roi
    assert dst.with_suffix(".yaml.bak").exists()
