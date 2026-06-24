from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from arm101_hand.config import load_system_camera_config
from arm101_hand.config.system_camera_config import HsvBand, RoiBox
from arm101_hand.system_camera.calibration import (
    ArcCase,
    arc_bands_from_circle,
    describe_case,
    deskew_crop,
    fit_camera_circle,
    pick_threshold,
    sample_red_band,
    screen_roi_from_rect,
    suggest_coverage_threshold,
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


def test_fit_camera_circle_centres_on_disc():
    img = np.zeros((480, 800, 3), dtype=np.uint8)
    cv2.circle(img, (400, 240), 180, (255, 255, 255), -1)
    cx, cy, r = fit_camera_circle(img)
    assert abs(cx - 400) <= 15 and abs(cy - 240) <= 15 and abs(r - 180) <= 25


def test_fit_camera_circle_handles_nonuniform_disc():
    # A LARGE disc (fills ~half the frame, like the real Aurora) that is BRIGHT on the right half and
    # DIM on the left -- both halves clearly above the dark bezel. Otsu splits bezel-from-disc and
    # recovers the WHOLE disc; a high-percentile threshold keeps only the bright half and mis-centres
    # it (the bench bug: percentile-80 fit cx=511 r=152 for a disc truly centred ~400 r~260). This is
    # the regression guard against the percentile approach.
    yy, xx = np.ogrid[:480, :800]
    disc = (xx - 400) ** 2 + (yy - 240) ** 2 <= 230**2
    gray = np.broadcast_to(np.where(xx < 400, 110, 230).astype(np.uint8), (480, 800)).copy()
    gray[~disc] = 0
    img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    cx, cy, r = fit_camera_circle(img)
    assert abs(cx - 400) <= 25 and abs(cy - 240) <= 25 and abs(r - 230) <= 35


def test_arc_bands_from_circle_are_symmetric():
    left, right = arc_bands_from_circle(400, 240, 200, ref_w=800, ref_h=480)
    assert left.x < 400 <= right.x
    assert (left.w, left.h) == (right.w, right.h)
    assert abs(right.x - (800 - (left.x + left.w))) <= 1  # mirror across centre


def test_arc_bands_mirror_about_circle_centre():
    # When the disc is NOT frame-centred, the bands must mirror about the CIRCLE centre cx (sitting on
    # the disc's own L/R edges), NOT about the frame centre. Bench bug: a circle fit at cx=511 produced
    # two bands clustered near the frame centre, missing both red arcs entirely.
    left, right = arc_bands_from_circle(300, 240, 150, ref_w=800, ref_h=480)
    assert (left.w, left.h) == (right.w, right.h)
    assert left.x < 300 < right.x  # bands straddle the circle centre
    assert abs((left.x + (right.x + right.w)) - 2 * 300) <= 2  # outer edges symmetric about cx=300


def test_sample_red_band_brackets_and_wraps():
    hsv = np.zeros((20, 20, 3), dtype=np.uint8)
    hsv[:, :10] = (2, 200, 200)  # near hue 0
    hsv[:, 10:] = (178, 200, 200)  # near hue 180
    bands = sample_red_band(cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR))
    assert len(bands) == 2
    assert any(b.h_lo == 0 for b in bands) and any(b.h_hi == 180 for b in bands)


def test_suggest_coverage_threshold_midpoint_and_floor():
    assert suggest_coverage_threshold(0.20, 0.0) == 0.10
    # Non-zero "off" coverage: catches a max()-instead-of-midpoint regression (0.15, not 0.20/0.10).
    assert suggest_coverage_threshold(0.20, 0.10) == 0.15
    assert suggest_coverage_threshold(0.02, 0.0) == 0.02  # midpoint 0.01 floored to 0.02


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
