from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from arm101_hand.config import load_system_camera_config
from arm101_hand.config.system_camera_config import HsvBand, RoiBox
from arm101_hand.system_camera import roi_from_region
from arm101_hand.system_camera.calibration import (
    ArcCase,
    describe_case,
    deskew_crop,
    pick_threshold,
    sample_red_band,
    screen_roi_from_rect,
    sweep_red_detection,
    write_calibration_values,
)

_DATA = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"


def test_screen_roi_from_rect_is_5_3_at_800x480_ref_with_angle():
    rect = ((800.0, 600.0), (400.0, 240.0), -1.0)  # in a 1600x1200 frame
    sr = screen_roi_from_rect(rect, 1600, 1200)
    assert (sr.ref_w, sr.ref_h) == (800, 480)
    assert sr.angle == -1.0
    # the stored box, scaled back to the frame, keeps ~5:3
    from arm101_hand.system_camera import roi_from_region

    x, y, w, h = roi_from_region(sr).for_frame(1600, 1200)
    assert abs((w / h) - 5 / 3) < 0.1


def test_deskew_crop_with_stored_angle_uprights_a_tilted_rect():
    # Deskew-SIGN regression guard for the manual rotate-to-deskew step: screen_roi_from_rect stores
    # +angle and Roi.crop/deskew_crop warps by +angle to produce an UPRIGHT crop. Synthesize a bright
    # 5:3-ish rect tilted by T deg as an interior island, build a screen_roi at that SAME tilt (PADDED
    # ~50% larger so the rect floats inside the crop -- otherwise it fills the bbox and minAreaRect
    # always reads -90 regardless of sign), deskew, and assert the bright region comes out axis-aligned.
    # A flipped deskew sign would leave it tilted ~2T deg (~20 deg) off axis, failing this. Decoupled
    # from the removed detector: the tilt is the known input, not a detection output.
    tilt = 10.0
    fw, fh = 800, 480
    big = np.zeros((fh, fw, 3), dtype=np.uint8)
    box = cv2.boxPoints(((400, 240), (300, 180), tilt)).astype(np.int32)
    cv2.fillPoly(big, [box], (255, 255, 255))
    screen_roi = screen_roi_from_rect(((400.0, 240.0), (300 * 1.5, 180 * 1.5), tilt), fw, fh)
    crop = deskew_crop(big, screen_roi, out=(800, 480))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    assert contours
    _, _, a = cv2.minAreaRect(max(contours, key=cv2.contourArea))
    # axis-aligned: minAreaRect angle ~0 deg or ~+/-90 deg (-90 is equivalent to axis-aligned)
    assert abs(a) < 1.5 or abs(abs(a) - 90.0) < 1.5


def test_sample_red_band_brackets_and_wraps():
    hsv = np.zeros((20, 20, 3), dtype=np.uint8)
    hsv[:, :10] = (2, 200, 200)  # near hue 0
    hsv[:, 10:] = (178, 200, 200)  # near hue 180
    bands = sample_red_band(cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR))
    assert len(bands) == 2
    assert any(b.h_lo == 0 for b in bands) and any(b.h_hi == 180 for b in bands)


def test_write_calibration_preserves_comments_and_updates(tmp_path):
    dst = tmp_path / "system_camera_config.yaml"
    dst.write_text(_DATA.read_text(encoding="utf-8"), encoding="utf-8")
    write_calibration_values(
        dst,
        screen_roi=RoiBox(x=10, y=8, w=400, h=240, ref_w=800, ref_h=480, angle=-0.9),
        left_arc=RoiBox(x=90, y=130, w=70, h=230, ref_w=800, ref_h=480),
        right_arc=RoiBox(x=640, y=130, w=70, h=230, ref_w=800, ref_h=480),
        red_bands=[HsvBand(h_lo=0, s_lo=40, v_lo=50, h_hi=10, s_hi=255, v_hi=255)],
        coverage_threshold=0.08,
    )
    text = dst.read_text(encoding="utf-8")
    assert "Automated trigger" in text and dst.with_suffix(".yaml.bak").exists()
    cfg = load_system_camera_config(dst)
    assert cfg.screen_roi.angle == -0.9 and (cfg.screen_roi.ref_w, cfg.screen_roi.ref_h) == (800, 480)
    assert cfg.auto_trigger.coverage_threshold == 0.08
    assert (cfg.auto_trigger.left_arc.x, cfg.auto_trigger.right_arc.x) == (90, 640)


def test_write_calibration_rejects_invalid_without_writing(tmp_path):
    dst = tmp_path / "system_camera_config.yaml"
    dst.write_text(_DATA.read_text(encoding="utf-8"), encoding="utf-8")
    before = dst.read_text(encoding="utf-8")
    with pytest.raises(ValidationError):
        write_calibration_values(
            dst,
            screen_roi=RoiBox(x=0, y=0, w=1, h=1),
            left_arc=RoiBox(x=0, y=0, w=1, h=1),
            right_arc=RoiBox(x=0, y=0, w=1, h=1),
            red_bands=[],  # empty -> rejected by the min_length=1 guard (a degenerate config)
            coverage_threshold=0.5,  # VALID: empty bands are the sole cause of rejection here
        )
    assert dst.read_text(encoding="utf-8") == before  # original untouched on failure


@pytest.mark.parametrize(
    "expected, det_left, det_right, want",
    [
        ("red", True, True, "both arcs correctly detected as red"),
        ("red", False, False, "both arcs incorrectly detected as not red"),
        ("red", True, False, "left arc correctly detected as red; right arc incorrectly detected as not red"),
        ("red", False, True, "left arc incorrectly detected as not red; right arc correctly detected as red"),
        ("clear", False, False, "both arcs correctly detected as not red"),
        ("clear", True, True, "both arcs incorrectly detected as red"),
        (
            "clear",
            True,
            False,
            "left arc incorrectly detected as red; right arc correctly detected as not red",
        ),
        (
            "clear",
            False,
            True,
            "left arc correctly detected as not red; right arc incorrectly detected as red",
        ),
    ],
)
def test_describe_case(expected, det_left, det_right, want):
    assert describe_case(expected, det_left, det_right) == want


def test_pick_threshold_separable_midpoint():
    t, sep = pick_threshold(red_covs=[0.20, 0.30], clear_covs=[0.00, 0.01])
    assert sep is True
    assert abs(t - 0.105) < 1e-9  # midpoint of max(clear)=0.01 and min(red)=0.20


def test_pick_threshold_overlap_minimises_misclassification():
    t, sep = pick_threshold(red_covs=[0.22, 0.30, 0.40], clear_covs=[0.00, 0.05, 0.25])
    assert sep is False
    assert abs(t - 0.135) < 1e-9  # min-error cut with the larger margin (between 0.05 and 0.22)


def test_pick_threshold_empty_returns_floor():
    assert pick_threshold([], [0.1]) == (0.02, True)
    assert pick_threshold([0.1], []) == (0.02, True)


def _roi_with_arcs(left, right, color_bgr):
    img = np.zeros((480, 800, 3), dtype=np.uint8)
    for arc in (left, right):
        img[arc.y : arc.y + arc.h, arc.x : arc.x + arc.w] = color_bgr
    return img


def test_sweep_red_detection_separates_red_from_clear():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=800, ref_h=480)
    right = RoiBox(x=600, y=120, w=80, h=240, ref_w=800, ref_h=480)
    red_frame = _roi_with_arcs(left, right, (0, 0, 200))  # pure red arcs (HSV hue 0)
    clear_frame = _roi_with_arcs(left, right, (80, 160, 80))  # greenish, no red
    res = sweep_red_detection([ArcCase(red_frame, "red"), ArcCase(clear_frame, "clear")], left, right)
    assert res.separable is True
    assert res.unsatisfied == []
    assert 0.0 < res.coverage_threshold < 1.0
    from arm101_hand.config.system_camera_config import AutoTriggerConfig
    from arm101_hand.system_camera.arc_detector import detect

    cfg = AutoTriggerConfig(
        left_arc=left, right_arc=right, red_bands=res.red_bands, coverage_threshold=res.coverage_threshold
    )
    assert detect(red_frame, cfg).both_red
    assert detect(clear_frame, cfg).both_clear


def test_sweep_red_detection_reports_unsatisfiable_cases():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=800, ref_h=480)
    right = RoiBox(x=600, y=120, w=80, h=240, ref_w=800, ref_h=480)
    red_frame = _roi_with_arcs(left, right, (0, 0, 200))
    contradictory_clear = _roi_with_arcs(left, right, (0, 0, 200))  # tagged 'clear' but actually red
    res = sweep_red_detection([ArcCase(red_frame, "red"), ArcCase(contradictory_clear, "clear")], left, right)
    assert res.separable is False
    assert res.unsatisfied  # non-empty -> best-effort, reported not silently dropped
    assert isinstance(res.coverage_threshold, float)


def _fill_arcs(left, right, hsv_color):
    """800x480 BGR frame with both arc boxes filled with a given HSV colour."""
    hsv = np.zeros((480, 800, 3), dtype=np.uint8)
    for arc in (left, right):
        hsv[arc.y : arc.y + arc.h, arc.x : arc.x + arc.w] = hsv_color
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_pooled_red_anchor_widens_hue_across_cases():
    from arm101_hand.system_camera.calibration import _pooled_red_anchor

    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=800, ref_h=480)
    right = RoiBox(x=600, y=120, w=80, h=240, ref_w=800, ref_h=480)
    near0 = _fill_arcs(left, right, (2, 200, 200))  # hue 2
    near180 = _fill_arcs(left, right, (178, 200, 200))  # hue 178 (wraps to red)
    # a single near-0 frame anchors only the near-zero band
    single = sample_red_band(roi_from_region(left).crop(near0))
    assert len(single) == 1 and single[0].h_lo == 0
    # pooling a near-0 frame and a near-180 frame yields BOTH wrap bands
    pooled = _pooled_red_anchor([near0, near180], left, right)
    assert len(pooled) == 2
    assert any(b.h_lo == 0 for b in pooled) and any(b.h_hi == 180 for b in pooled)


def test_sweep_excludes_transitional_red_case():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=800, ref_h=480)
    right = RoiBox(x=600, y=120, w=80, h=240, ref_w=800, ref_h=480)
    red_frame = _roi_with_arcs(left, right, (0, 0, 200))  # both arcs red
    clear_frame = _roi_with_arcs(left, right, (80, 160, 80))  # both greenish/clear
    transitional = _roi_with_arcs(left, right, (80, 160, 80))  # start greenish...
    transitional[left.y : left.y + left.h, left.x : left.x + left.w] = (0, 0, 200)  # ...left arc red only
    cases = [ArcCase(red_frame, "red"), ArcCase(clear_frame, "clear"), ArcCase(transitional, "red")]
    res = sweep_red_detection(cases, left, right)
    assert res.separable is True  # the clean both-red + clear separate
    assert 2 in res.excluded_transitional  # the one-arc-red 'red' frame is excluded from fitting
    assert 2 not in res.unsatisfied  # and is not counted as a fit failure
    from arm101_hand.config.system_camera_config import AutoTriggerConfig
    from arm101_hand.system_camera.arc_detector import detect

    cfg = AutoTriggerConfig(
        left_arc=left, right_arc=right, red_bands=res.red_bands, coverage_threshold=res.coverage_threshold
    )
    assert detect(red_frame, cfg).both_red and detect(clear_frame, cfg).both_clear


def test_sweep_pooled_hue_classifies_two_different_red_hues():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=800, ref_h=480)
    right = RoiBox(x=600, y=120, w=80, h=240, ref_w=800, ref_h=480)
    red_a = _fill_arcs(left, right, (2, 200, 200))  # hue 2
    red_b = _fill_arcs(left, right, (178, 200, 200))  # hue 178 (wraps to red)
    clear = _roi_with_arcs(left, right, (80, 160, 80))
    res = sweep_red_detection(
        [ArcCase(red_a, "red"), ArcCase(red_b, "red"), ArcCase(clear, "clear")], left, right
    )
    assert res.separable is True
    from arm101_hand.config.system_camera_config import AutoTriggerConfig
    from arm101_hand.system_camera.arc_detector import detect

    cfg = AutoTriggerConfig(
        left_arc=left, right_arc=right, red_bands=res.red_bands, coverage_threshold=res.coverage_threshold
    )
    assert detect(red_a, cfg).both_red and detect(red_b, cfg).both_red and detect(clear, cfg).both_clear
