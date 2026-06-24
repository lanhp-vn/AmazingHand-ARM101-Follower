"""Unit test for the arc-debug diagnostic's pure sidecar-builder.

The builder lives in scripts/diagnostics/system_camera/usb_camera_arc_debug.py. scripts/ is not
an importable package here (scripts add src/ to sys.path and import arm101_hand; nothing imports a
script *module*), so we load the function via a path-based import. Safe because the script's heavy
work (run_grab_demo, camera open) is inside main()/guarded by __main__ -- module top-level only
defines functions + constants.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

from arm101_hand.config.system_camera_config import AutoTriggerConfig, RoiBox
from arm101_hand.system_camera.arc_detector import AlignmentState

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "diagnostics"
    / "system_camera"
    / "usb_camera_arc_debug.py"
)


def _load_builder():
    spec = importlib.util.spec_from_file_location("usb_camera_arc_debug", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_arc_case_sidecar


def test_build_arc_case_sidecar_shape():
    build = _load_builder()
    alignment = AlignmentState(left_red=True, right_red=False, left_cov=0.061, right_cov=0.004)
    cfg = AutoTriggerConfig(
        left_arc=RoiBox(x=115, y=130, w=56, h=230, ref_w=640, ref_h=480),
        right_arc=RoiBox(x=469, y=130, w=56, h=230, ref_w=640, ref_h=480),
        coverage_threshold=0.02,
    )
    screen_roi = RoiBox(x=82, y=89, w=162, h=97, ref_w=640, ref_h=480, angle=-2.045)
    ts = dt.datetime(2026, 6, 23, 14, 5, 33, tzinfo=dt.UTC)

    out = build(
        alignment,
        cfg,
        screen_roi,
        camera_index=1,
        backend="dshow",
        frame_w=1600,
        frame_h=1200,
        captured_at=ts,
    )

    assert out["tool"] == "usb_camera_arc_debug"
    assert out["captured_at"] == "2026-06-23T14:05:33+00:00"
    assert out["camera"] == {"index": 1, "backend": "dshow", "frame_w": 1600, "frame_h": 1200}

    det = out["detection"]
    assert det["left_red"] is True and det["right_red"] is False
    assert det["left_cov"] == 0.061 and det["right_cov"] == 0.004
    assert det["both_red"] is False and det["both_clear"] is False
    assert det["coverage_threshold"] == 0.02

    assert out["screen_roi"] == screen_roi.model_dump()
    assert out["left_arc"] == cfg.left_arc.model_dump()
    assert out["right_arc"] == cfg.right_arc.model_dump()
    assert out["a_star_min"] == cfg.a_star_min
    assert out["morph_kernel"] == cfg.morph_kernel
    assert out["expected_note"] == ""  # default when no note is passed (label-later still possible)


def test_build_arc_case_sidecar_records_operator_note():
    build = _load_builder()
    alignment = AlignmentState(left_red=False, right_red=False, left_cov=0.001, right_cov=0.002)
    cfg = AutoTriggerConfig()
    screen_roi = RoiBox(x=0, y=0, w=10, h=10)
    ts = dt.datetime(2026, 6, 23, 21, 46, 18, tzinfo=dt.UTC)

    out = build(
        alignment,
        cfg,
        screen_roi,
        camera_index=1,
        backend="dshow",
        frame_w=1600,
        frame_h=1200,
        captured_at=ts,
        expected_note="both arcs are vividly red to the eye but read clear",
    )

    assert out["expected_note"] == "both arcs are vividly red to the eye but read clear"
