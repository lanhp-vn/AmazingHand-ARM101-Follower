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

import io
from pathlib import Path

import cv2
import numpy as np
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from arm101_hand.config.system_camera_config import HsvBand, RoiBox, SystemCameraConfig

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


# Broad colour priors (the schema defaults) used to FIND candidate pixels before percentile
# sampling tightens the band. Red wraps hue 0/180 -> two prior bands.
_RED_PRIOR: list[HsvBand] = [
    HsvBand(h_lo=0, s_lo=80, v_lo=80, h_hi=10, s_hi=255, v_hi=255),
    HsvBand(h_lo=170, s_lo=80, v_lo=80, h_hi=180, s_hi=255, v_hi=255),
]
_GREEN_PRIOR: list[HsvBand] = [HsvBand(h_lo=35, s_lo=40, v_lo=50, h_hi=95, s_hi=255, v_hi=255)]


def _prior_mask(hsv: np.ndarray, bands: list[HsvBand]) -> np.ndarray:
    """OR the inRange masks for every prior band, then MORPH_OPEN(3) to despeckle."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for b in bands:
        lo = np.array([b.h_lo, b.s_lo, b.v_lo], dtype=np.uint8)
        hi = np.array([b.h_hi, b.s_hi, b.v_hi], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)


def _bbox_of_nonzero(half: np.ndarray, pad: int, full_w: int, full_h: int, x_off: int) -> RoiBox:
    ys, xs = np.nonzero(half)
    if xs.size == 0:
        raise ValueError("no matching pixels in this half of the ROI")
    x0, x1 = int(xs.min()) + x_off, int(xs.max()) + x_off
    y0, y1 = int(ys.min()), int(ys.max())
    x = max(0, x0 - pad)
    y = max(0, y0 - pad)
    w = min(full_w - x, (x1 - x0) + 1 + 2 * pad)
    h = min(full_h - y, (y1 - y0) + 1 + 2 * pad)
    return RoiBox(x=x, y=y, w=w, h=h, ref_w=full_w, ref_h=full_h)


def detect_arc_regions(
    roi_ref_bgr: np.ndarray, *, red_prior: list[HsvBand] | None = None, pad: int = 4
) -> tuple[RoiBox, RoiBox]:
    """``(left_arc, right_arc)`` regions found from red pixels in the left/right halves of the ROI.

    ``roi_ref_bgr`` is the chosen ROI crop resized to the detection reference. Raises ``ValueError``
    if either half has no red pixels (caller offers redo / manual selectROI)."""
    prior = red_prior if red_prior is not None else _RED_PRIOR
    hsv = cv2.cvtColor(roi_ref_bgr, cv2.COLOR_BGR2HSV)
    mask = _prior_mask(hsv, prior)
    h, w = mask.shape
    mid = w // 2
    left = _bbox_of_nonzero(mask[:, :mid], pad, w, h, x_off=0)
    right = _bbox_of_nonzero(mask[:, mid:], pad, w, h, x_off=mid)
    return left, right


def sample_hsv_band(
    region_bgr: np.ndarray, color: str, *, lo_pct: float = 5, hi_pct: float = 95
) -> list[HsvBand]:
    """Bracket the matched pixels' HSV percentiles into 1 band (green) or up to 2 (red hue-wrap).

    ``color`` is ``"red"`` or ``"green"``. Raises ``ValueError`` if no pixels match the prior."""
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    prior = _RED_PRIOR if color == "red" else _GREEN_PRIOR
    pts = hsv[_prior_mask(hsv, prior) > 0]
    if pts.size == 0:
        raise ValueError(f"no {color} pixels to sample in this region")
    s_lo = int(np.percentile(pts[:, 1], lo_pct))
    v_lo = int(np.percentile(pts[:, 2], lo_pct))
    hue = pts[:, 0]
    if color == "green":
        h_lo = int(np.percentile(hue, lo_pct))
        h_hi = int(np.percentile(hue, hi_pct))
        return [HsvBand(h_lo=h_lo, s_lo=s_lo, v_lo=v_lo, h_hi=h_hi, s_hi=255, v_hi=255)]
    bands: list[HsvBand] = []
    near_zero = hue[hue <= 90]
    near_180 = hue[hue > 90]
    if near_zero.size:
        bands.append(
            HsvBand(
                h_lo=0, s_lo=s_lo, v_lo=v_lo, h_hi=int(np.percentile(near_zero, hi_pct)), s_hi=255, v_hi=255
            )
        )
    if near_180.size:
        bands.append(
            HsvBand(
                h_lo=int(np.percentile(near_180, lo_pct)), s_lo=s_lo, v_lo=v_lo, h_hi=180, s_hi=255, v_hi=255
            )
        )
    return bands


def suggest_coverage_threshold(
    green_cov_on_green: float, green_cov_on_red: float, *, floor: float = 0.02
) -> float:
    """Midpoint between the green-frame (high) and red-frame (low) green coverage, floored."""
    return round(max((green_cov_on_green + green_cov_on_red) / 2.0, floor), 4)


def _flow_map(d: dict[str, int]) -> CommentedMap:
    """A ruamel mapping rendered inline ``{a: 1, b: 2}`` to match the file's arc/band style."""
    m = CommentedMap(d)
    m.fa.set_flow_style()
    return m


def _roibox_map(r: RoiBox) -> CommentedMap:
    return _flow_map({"x": r.x, "y": r.y, "w": r.w, "h": r.h, "ref_w": r.ref_w, "ref_h": r.ref_h})


def _hsv_map(b: HsvBand) -> CommentedMap:
    return _flow_map(
        {"h_lo": b.h_lo, "s_lo": b.s_lo, "v_lo": b.v_lo, "h_hi": b.h_hi, "s_hi": b.s_hi, "v_hi": b.v_hi}
    )


def write_calibration_values(
    config_path: Path,
    *,
    screen_roi: RoiBox,
    left_arc: RoiBox,
    right_arc: RoiBox,
    red_bands: list[HsvBand],
    green_bands: list[HsvBand],
    coverage_threshold: float,
) -> None:
    """Round-trip ``system_camera_config.yaml`` (preserving comments), updating screen_roi + the
    auto_trigger regions/bands/threshold. Validates the result via pydantic and writes a ``.bak``
    copy BEFORE touching the original; raises (without writing) if validation fails."""
    yaml_rt = YAML()  # round-trip mode preserves comments + key order
    yaml_rt.preserve_quotes = True
    data = yaml_rt.load(config_path.read_text(encoding="utf-8"))

    data["screen_roi"] = _roibox_map(screen_roi)
    at = data["auto_trigger"]
    at["left_arc"] = _roibox_map(left_arc)
    at["right_arc"] = _roibox_map(right_arc)
    at["red_bands"] = [_hsv_map(b) for b in red_bands]
    at["green_bands"] = [_hsv_map(b) for b in green_bands]
    at["coverage_threshold"] = float(coverage_threshold)

    # Validate the would-be file via pydantic BEFORE writing (plain safe_load of the dumped text).
    buf = io.StringIO()
    yaml_rt.dump(data, buf)
    SystemCameraConfig.model_validate(yaml.safe_load(buf.getvalue()))

    config_path.with_suffix(config_path.suffix + ".bak").write_text(
        config_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with config_path.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)
