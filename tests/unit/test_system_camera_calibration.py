import cv2
import numpy as np
import pytest  # noqa: F401  # used by Task 5 tests

from arm101_hand.system_camera.calibration import detect_screen_rect, to_roi_candidate


def test_detect_screen_rect_ranks_screen_above_distractor():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(img, (100, 80), (340, 260), (255, 255, 255), -1)  # the screen (240x180)
    cv2.rectangle(img, (500, 400), (560, 440), (255, 255, 255), -1)  # small bright distractor
    rects = detect_screen_rect(img)
    assert len(rects) >= 1
    x, y, w, h = rects[0]
    assert 90 <= x <= 110 and 70 <= y <= 90  # the screen, not the distractor
    assert w > h  # landscape


def test_detect_screen_rect_returns_at_most_top_n():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    for i in range(5):
        cv2.rectangle(img, (10 + i * 110, 10), (90 + i * 110, 120), (255, 255, 255), -1)
    assert len(detect_screen_rect(img, top_n=3)) <= 3


def test_to_roi_candidate_normalizes_to_4_3_within_bounds():
    x, y, w, h = to_roi_candidate((100, 100, 200, 100), 640, 480)  # 2:1 -> expand height
    assert abs((w / h) - 4 / 3) < 0.05
    assert x >= 0 and y >= 0 and x + w <= 640 and y + h <= 480
