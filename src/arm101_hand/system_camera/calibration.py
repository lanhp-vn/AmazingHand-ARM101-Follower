"""Pure detection helpers for the system-camera view calibration (device layer).

Re-derives the Aurora screen ROI, the two alignment-arc regions, and the red/green HSV bands
from three sample frames (white startup screen, red arcs, green arcs). Pure numpy + cv2 imgproc
-- no HighGUI window; the one I/O function is ``write_calibration_values`` (ruamel round-trip).
Synthetic-numpy unit-testable, mirroring ``arc_detector.py``. The interactive capture/confirm
shell lives in ``scripts/calibration/system_camera/calibrate_view.py``.

Rectangle detection adapts references/computer-vision/opencv/samples/python/squares.py
(threshold -> findContours -> approxPolyDP -> area/convexity filter). Plain opencv-python only.
"""

from __future__ import annotations

import cv2
import numpy as np

from arm101_hand.config.system_camera_config import HsvBand  # noqa: F401  # used by Task 5 helpers

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _screen_likeness(contour: np.ndarray, frame_area: int) -> float:
    """Score a bright blob on how screen-like it is: solid, rectangular, sizeable, landscape."""
    area = cv2.contourArea(contour)
    if area <= 0:
        return 0.0
    x, y, w, h = cv2.boundingRect(contour)
    rect_area = w * h
    fill = area / rect_area if rect_area else 0.0
    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    corner = 1.0 if len(approx) == 4 else 0.6 if len(approx) <= 6 else 0.25
    size = min(area / frame_area / 0.10, 1.0)  # rewards >= ~10% of the frame
    aspect = w / h if h else 0.0
    aspect_score = 1.0 if 1.0 <= aspect <= 2.2 else 0.3  # the Aurora screen is landscape-ish
    return fill * corner * size * aspect_score


def detect_screen_rect(white_bgr: np.ndarray, *, top_n: int = 3) -> list[tuple[int, int, int, int]]:
    """Ranked candidate ``(x, y, w, h)`` bounding boxes of the bright screen, best first.

    Otsu-thresholds the frame, closes gaps (logo/knob/text), finds external contours, and ranks
    them by :func:`_screen_likeness`. Returns up to ``top_n`` boxes with a positive score.
    """
    gray = cv2.cvtColor(white_bgr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    fh, fw = white_bgr.shape[:2]
    frame_area = fw * fh
    scored = sorted(
        ((_screen_likeness(c, frame_area), cv2.boundingRect(c)) for c in contours),
        key=lambda t: t[0],
        reverse=True,
    )
    return [bbox for score, bbox in scored if score > 0.0][:top_n]


def to_roi_candidate(
    bbox: tuple[int, int, int, int], frame_w: int, frame_h: int, *, target_aspect: float = 4 / 3
) -> tuple[int, int, int, int]:
    """Expand ``bbox`` about its centre to ``target_aspect`` (never shrinks -> no content lost),
    then clamp inside the frame. Distortion-free because the crop is later resized to a same-aspect
    reference."""
    x, y, w, h = bbox
    cx, cy = x + w / 2.0, y + h / 2.0
    fw, fh = float(w), float(h)
    if fw / fh < target_aspect:
        fw = target_aspect * fh
    else:
        fh = fw / target_aspect
    nx = int(round(cx - fw / 2.0))
    ny = int(round(cy - fh / 2.0))
    nw = int(round(fw))
    nh = int(round(fh))
    nx = max(0, min(nx, frame_w - 1))
    ny = max(0, min(ny, frame_h - 1))
    nw = max(1, min(nw, frame_w - nx))
    nh = max(1, min(nh, frame_h - ny))
    return nx, ny, nw, nh
