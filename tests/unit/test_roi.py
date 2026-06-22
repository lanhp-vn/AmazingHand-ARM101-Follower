import numpy as np

from arm101_hand.system_camera import AURORA_SCREEN_ROI, Roi


def _frame(w: int, h: int) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_identity_at_reference_size():
    # At the reference resolution the ROI passes through unchanged.
    roi = Roi(130, 6, 280, 210, ref_w=640, ref_h=480)
    assert roi.for_frame(640, 480) == (130, 6, 280, 210)


def test_uniform_scale_same_aspect():
    # A 2x-larger frame of the same aspect scales every component by 2.
    roi = Roi(130, 6, 280, 210, ref_w=640, ref_h=480)
    assert roi.for_frame(1280, 960) == (260, 12, 560, 420)


def test_nonuniform_scale_per_axis():
    # Different aspect: x/w scale by the width ratio, y/h by the height ratio (independently).
    roi = Roi(130, 6, 280, 210, ref_w=640, ref_h=480)
    assert roi.for_frame(1280, 720) == (260, 9, 560, 315)


def test_clamps_inside_frame():
    # An ROI that would overflow the bottom-right is clamped to stay in-bounds.
    roi = Roi(600, 440, 100, 100, ref_w=640, ref_h=480)
    x, y, w, h = roi.for_frame(640, 480)
    assert (x, y, w, h) == (600, 440, 40, 40)
    assert x + w <= 640 and y + h <= 480


def test_crop_shape_matches_for_frame():
    roi = Roi(130, 6, 280, 210, ref_w=640, ref_h=480)
    assert roi.crop(_frame(640, 480)).shape == (210, 280, 3)
    # Scaled frame: crop shape tracks the rescaled ROI (h, w from for_frame).
    assert roi.crop(_frame(1280, 960)).shape == (420, 560, 3)


def test_aurora_roi_downscales_at_2592x1944():
    # The 2592x1944 stream (system_camera_config.yaml) makes the ROI crop LARGER than the 640x480
    # reference, so the resize-to-reference is a DOWNSCALE -- real pixels, no upscale. This is the
    # whole point of the 2K stream; it locks the for_frame numbers the config relies on.
    x, y, w, h = AURORA_SCREEN_ROI.for_frame(2592, 1944)
    assert (x, y, w, h) == (243, 304, 794, 595)
    assert w >= AURORA_SCREEN_ROI.ref_w and h >= AURORA_SCREEN_ROI.ref_h  # no upscale in either axis
