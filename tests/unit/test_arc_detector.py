import numpy as np

from arm101_hand.config.system_camera_config import AutoTriggerConfig
from arm101_hand.system_camera.arc_detector import detect

_GREEN = (0, 255, 0)  # BGR
_RED = (0, 0, 255)
_WHITE = (255, 255, 255)


def _frame(left_bgr=None, right_bgr=None, *, fill=1.0):
    """640x480 black frame; fill the configured arc bands with the given BGR colour.
    ``fill`` is the fraction of each band's height to paint (from the top)."""
    cfg = AutoTriggerConfig()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    for region, color in ((cfg.left_arc, left_bgr), (cfg.right_arc, right_bgr)):
        if color is None:
            continue
        rows = max(1, int(region.h * fill))
        frame[region.y : region.y + rows, region.x : region.x + region.w] = color
    return cfg, frame


def test_both_green_ready():
    cfg, frame = _frame(_GREEN, _GREEN)
    st = detect(frame, cfg)
    assert st.left == "GREEN" and st.right == "GREEN"
    assert st.ready is True


def test_both_red_not_ready():
    cfg, frame = _frame(_RED, _RED)
    st = detect(frame, cfg)
    assert st.left == "RED" and st.right == "RED"
    assert st.ready is False


def test_either_green_is_ready():
    cfg, frame = _frame(_RED, _GREEN)
    st = detect(frame, cfg)
    assert st.left == "RED" and st.right == "GREEN"
    assert st.ready is True  # either-arc-green rule


def test_require_no_red_blocks_mixed():
    cfg, frame = _frame(_RED, _GREEN)
    cfg = cfg.model_copy(update={"require_no_red": True})
    st = detect(frame, cfg)
    assert st.ready is False


def test_white_glare_is_none():
    cfg, frame = _frame(_WHITE, _WHITE)
    st = detect(frame, cfg)
    assert st.left == "NONE" and st.right == "NONE"
    assert st.ready is False


def test_below_coverage_threshold_is_none():
    cfg, frame = _frame(_GREEN, None, fill=0.02)  # ~2% < 4% threshold
    st = detect(frame, cfg)
    assert st.left == "NONE"


def test_above_coverage_threshold_is_green():
    cfg, frame = _frame(_GREEN, None, fill=0.07)  # ~7% > 4% threshold
    st = detect(frame, cfg)
    assert st.left == "GREEN"
