import numpy as np

from arm101_hand.config.system_camera_config import AutoTriggerConfig, RoiBox
from arm101_hand.system_camera.arc_detector import AlignmentState, detect

_RED = (0, 0, 255)


def _cfg() -> AutoTriggerConfig:
    # arcs at the left/right thirds of an 800x480 reference frame
    return AutoTriggerConfig(
        left_arc=RoiBox(x=40, y=160, w=120, h=160, ref_w=800, ref_h=480),
        right_arc=RoiBox(x=640, y=160, w=120, h=160, ref_w=800, ref_h=480),
        coverage_threshold=0.2,
    )


def test_alignmentstate_properties():
    assert AlignmentState(True, True, 0.5, 0.5).both_red is True
    assert AlignmentState(False, False, 0.0, 0.0).both_clear is True
    assert AlignmentState(True, False, 0.5, 0.0).both_red is False
    assert AlignmentState(True, False, 0.5, 0.0).both_clear is False


def test_detect_both_red():
    frame = np.zeros((480, 800, 3), dtype=np.uint8)
    frame[160:320, 40:160] = _RED
    frame[160:320, 640:760] = _RED
    s = detect(frame, _cfg())
    assert s.both_red is True


def test_detect_both_clear_on_blank():
    frame = np.zeros((480, 800, 3), dtype=np.uint8)  # nothing red
    s = detect(frame, _cfg())
    assert s.both_clear is True


def test_detect_respects_coverage_threshold():
    # Pin the coverage gate itself (lc >= threshold), not just saturated/blank extremes: paint the
    # left arc band ~10% red (below the 0.2 threshold -> not red) then ~40% (above -> red).
    cfg = _cfg()  # coverage_threshold=0.2
    arc = cfg.left_arc

    def left_red_for(fill: float) -> bool:
        frame = np.zeros((480, 800, 3), dtype=np.uint8)
        rows = int(fill * arc.h)
        frame[arc.y : arc.y + rows, arc.x : arc.x + arc.w] = _RED
        return detect(frame, cfg).left_red

    assert left_red_for(0.10) is False  # 10% red < 0.2 threshold
    assert left_red_for(0.40) is True  # 40% red > 0.2 threshold
