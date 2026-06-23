"""Pure detection helpers for the system-camera view calibration (device layer).

Re-derives the Aurora screen ROI, the two alignment-arc regions, and the red/green HSV bands
from three sample frames (white startup screen, red arcs, green arcs). Pure numpy + cv2 imgproc
-- no HighGUI window; the one I/O function is ``write_calibration_values`` (ruamel round-trip).
Synthetic-numpy unit-testable, mirroring ``arc_detector.py``. The interactive capture/confirm
shell lives in ``scripts/calibration/system_camera/calibrate_view.py``.

Screen detection sweeps several HIGH brightness thresholds (the backlit Aurora LCD is near-
saturated white, unlike painted walls / daylight) and ranks INTERIOR rectangles by
fill x aspect x size x not-touching-the-border -- the screen is the only large bright rectangle
that does not run into the frame edge. Plain opencv-python only (no contrib); patterns adapted
from the OpenCV samples, never imported from references/.
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

from .roi import roi_from_region

# Screen detection tuning. The Aurora screen is a backlit LCD (near-saturated white) sitting as an
# island inside the frame; walls / daylight / doorway are bright too but run into the frame border.
_SCREEN_ASPECT_LO, _SCREEN_ASPECT_HI = 1.2, 2.0  # landscape ~16:10..16:9
_SCREEN_MIN_AREA_FRAC = 0.01  # ignore blobs smaller than 1% of the frame
_SCREEN_BORDER_FRAC = 0.01  # within 1% of an edge counts as "touching the border"
_SCREEN_TARGET_AREA_FRAC = 0.04  # size score saturates around 4% of the frame
_SCREEN_THRESH_FRACS = (0.4, 0.6, 0.8)  # high cuts between Otsu and 255 to isolate the backlit LCD


def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-union of two ``(x, y, w, h)`` boxes (to de-dupe candidates across cuts)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix0, iy0 = max(ax, bx), max(ay, by)
    ix1, iy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _screen_likeness(bbox: tuple[int, int, int, int], area: float, frame_w: int, frame_h: int) -> float:
    """Score a bright blob on how screen-like it is: solid (high fill), landscape aspect, sizeable,
    and INTERIOR -- not touching the frame border (walls / daylight do; the screen does not)."""
    x, y, w, h = bbox
    rect_area = w * h
    if rect_area <= 0:
        return 0.0
    fill = area / rect_area
    aspect = w / h if h else 0.0
    aspect_score = 1.0 if _SCREEN_ASPECT_LO <= aspect <= _SCREEN_ASPECT_HI else 0.25
    size = min(area / (frame_w * frame_h) / _SCREEN_TARGET_AREA_FRAC, 1.0)
    margin = max(2, int(_SCREEN_BORDER_FRAC * min(frame_w, frame_h)))
    touches = x <= margin or y <= margin or x + w >= frame_w - margin or y + h >= frame_h - margin
    interior = 0.1 if touches else 1.0
    return fill * aspect_score * size * interior


_Rect = tuple[tuple[float, float], tuple[float, float], float]


def _norm_rect(rect: _Rect) -> _Rect:
    """Normalise a ``minAreaRect`` to width >= height and angle in (-45, 45].

    OpenCV 4.13's ``minAreaRect`` returns the angle in the post-4.5 (0, 90] convention. Swapping the
    sides when ``w < h`` (and folding the angle by 90 deg) recovers the small physical tilt of a
    landscape screen instead of an arbitrary +/-90 deg rotation."""
    (cx, cy), (w, h), angle = rect
    if w < h:
        w, h = h, w
        angle += 90.0
    if angle > 45.0:
        angle -= 90.0
    elif angle < -45.0:
        angle += 90.0
    return (cx, cy), (w, h), angle


def detect_screen_rects(white_bgr: np.ndarray, *, top_n: int = 3) -> list[_Rect]:
    """Top-``top_n`` interior bright rotated rects ``((cx,cy),(w,h),angle)``, best first.

    Same high-threshold + interior + screen-likeness ranking as before, but returns the rotated
    ``minAreaRect`` (so the slight screen tilt is recovered) instead of an axis-aligned bbox."""
    gray = cv2.cvtColor(white_bgr, cv2.COLOR_BGR2GRAY)
    fh, fw = gray.shape
    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    min_area = _SCREEN_MIN_AREA_FRAC * fw * fh
    scored: list[tuple[float, _Rect]] = []
    for frac in _SCREEN_THRESH_FRACS:
        thr = int(otsu + (255 - otsu) * frac)
        _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            bbox = cv2.boundingRect(c)
            s = _screen_likeness(bbox, area, fw, fh)
            if s > 0.0:
                scored.append((s, _norm_rect(cv2.minAreaRect(c))))
    scored.sort(key=lambda t: t[0], reverse=True)
    out: list[_Rect] = []
    seen: list[tuple[int, int, int, int]] = []
    for _, rect in scored:
        bbox = (
            int(rect[0][0] - rect[1][0] / 2),
            int(rect[0][1] - rect[1][1] / 2),
            int(rect[1][0]),
            int(rect[1][1]),
        )
        if all(_bbox_iou(bbox, b) < 0.5 for b in seen):
            seen.append(bbox)
            out.append(rect)
        if len(out) >= top_n:
            break
    return out


def screen_roi_from_rect(
    rect: _Rect, frame_w: int, frame_h: int, *, ref_w: int = 800, ref_h: int = 480
) -> RoiBox:
    """Normalise a rotated screen rect to 5:3 and store it at the ``ref_w x ref_h`` detection ref
    (so ``Roi.crop`` -> resize lands a 5:3 deskewed image). Carries the deskew ``angle``."""
    (cx, cy), (w, h), angle = rect
    target = 5 / 3
    if w / h < target:  # widen the short side to 5:3 (never shrink -> no content lost)
        w = target * h
    else:
        h = w / target
    sx, sy = ref_w / frame_w, ref_h / frame_h
    bx = int(round((cx - w / 2) * sx))
    by = int(round((cy - h / 2) * sy))
    return RoiBox(
        x=max(0, bx),
        y=max(0, by),
        w=max(1, int(round(w * sx))),
        h=max(1, int(round(h * sy))),
        ref_w=ref_w,
        ref_h=ref_h,
        angle=float(angle),
    )


def deskew_crop(frame: np.ndarray, region: RoiBox, *, out: tuple[int, int] = (800, 480)) -> np.ndarray:
    """Deskewed 5:3 crop of ``frame`` for ``region`` (screen_roi), resized to ``out``."""
    return cv2.resize(roi_from_region(region).crop(frame), out, interpolation=cv2.INTER_AREA)


def fit_camera_circle(bright_ref_bgr: np.ndarray) -> tuple[float, float, float]:
    """Centre + radius of the bright central disc in a deskewed ``out``-sized aligned frame.

    Thresholds the bright disc, takes the largest blob, and fits a circle via the contour's area
    (radius = sqrt(area/pi)) about its centroid -- tighter than minEnclosingCircle, which the glare
    streak inflates."""
    g = cv2.cvtColor(bright_ref_bgr, cv2.COLOR_BGR2GRAY)
    # Cap below 255: THRESH_BINARY keeps src > thresh, so a percentile of 255 (disc much brighter than
    # the surround) would otherwise threshold to an empty mask.
    thr = min(int(np.percentile(g, 80)), 254)
    _, m = cv2.threshold(g, thr, 255, cv2.THRESH_BINARY)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("no bright disc found for the camera circle")
    disc = max(contours, key=cv2.contourArea)
    mo = cv2.moments(disc)
    area = cv2.contourArea(disc)
    cx = mo["m10"] / mo["m00"] if mo["m00"] else bright_ref_bgr.shape[1] / 2
    cy = mo["m01"] / mo["m00"] if mo["m00"] else bright_ref_bgr.shape[0] / 2
    r = float(np.sqrt(area / np.pi))
    return float(cx), float(cy), r


def arc_bands_from_circle(
    cx: float, cy: float, r: float, *, ref_w: int = 800, ref_h: int = 480, pad: int = 4
) -> tuple[RoiBox, RoiBox]:
    """Two mirror-symmetric bands on the circle's left/right edges (outer ~35% of the radius,
    middle 60% vertically) -- excludes the top corners (battery) and the top/bottom of the disc."""
    bw = max(1, int(round(0.35 * r)))
    y = max(0, int(round(cy - 0.6 * r)) - pad)
    h = min(ref_h - y, int(round(1.2 * r)) + 2 * pad)
    lx = max(0, int(round(cx - r)) - pad)
    # enforce exact mirror symmetry about the frame centre using the left band
    rx = ref_w - (lx + bw)
    left = RoiBox(x=lx, y=y, w=bw, h=h, ref_w=ref_w, ref_h=ref_h)
    right = RoiBox(x=max(0, rx), y=y, w=bw, h=h, ref_w=ref_w, ref_h=ref_h)
    return left, right


# Broad colour prior used to FIND candidate red arc pixels before percentile sampling tightens the
# band. Deliberately GENEROUS on saturation/value: the Aurora alignment arcs are PALE (low-S) and
# bright, sitting over a tinted near-white disc -- bench captures measured red arcs at S~50 V~227
# hue~170, so tight S/V floors miss them. Red wraps hue 0/180 -> two bands.
_RED_PRIOR: list[HsvBand] = [
    HsvBand(h_lo=0, s_lo=35, v_lo=50, h_hi=12, s_hi=255, v_hi=255),
    HsvBand(h_lo=158, s_lo=35, v_lo=50, h_hi=180, s_hi=255, v_hi=255),
]


def _prior_mask(hsv: np.ndarray, bands: list[HsvBand]) -> np.ndarray:
    """OR the inRange masks for every prior band, then MORPH_OPEN(3) to despeckle."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for b in bands:
        lo = np.array([b.h_lo, b.s_lo, b.v_lo], dtype=np.uint8)
        hi = np.array([b.h_hi, b.s_hi, b.v_hi], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)


def sample_red_band(region_bgr: np.ndarray, *, lo_pct: float = 5, hi_pct: float = 95) -> list[HsvBand]:
    """Percentile-bracket the red arc pixels into 1-2 bands (0/180 hue wrap). Raises if none match."""
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    pts = hsv[_prior_mask(hsv, _RED_PRIOR) > 0]
    if pts.size == 0:
        raise ValueError("no red pixels to sample in this region")
    s_lo = int(np.percentile(pts[:, 1], lo_pct))
    v_lo = int(np.percentile(pts[:, 2], lo_pct))
    hue = pts[:, 0]
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
    red_cov_on_red: float, red_cov_on_bright: float, *, floor: float = 0.02
) -> float:
    """Midpoint between the red-frame (high) and bright-frame (low) red coverage, floored."""
    return round(max((red_cov_on_red + red_cov_on_bright) / 2.0, floor), 4)


def _flow_map(d: dict[str, float]) -> CommentedMap:
    """A ruamel mapping rendered inline ``{a: 1, b: 2}`` to match the file's arc/band style."""
    m = CommentedMap(d)
    m.fa.set_flow_style()
    return m


def _roibox_map(r: RoiBox) -> CommentedMap:
    return _flow_map(
        {"x": r.x, "y": r.y, "w": r.w, "h": r.h, "ref_w": r.ref_w, "ref_h": r.ref_h, "angle": r.angle}
    )


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
    coverage_threshold: float,
) -> None:
    """Round-trip ``system_camera_config.yaml`` (preserving comments), updating screen_roi + the
    auto_trigger arc regions / red bands / threshold. Validates the result via pydantic and writes a
    ``.bak`` copy BEFORE touching the original; raises (without writing) if validation fails."""
    yaml_rt = YAML()  # round-trip mode preserves comments + key order
    yaml_rt.preserve_quotes = True
    data = yaml_rt.load(config_path.read_text(encoding="utf-8"))

    data["screen_roi"] = _roibox_map(screen_roi)
    at = data["auto_trigger"]
    at["left_arc"] = _roibox_map(left_arc)
    at["right_arc"] = _roibox_map(right_arc)
    at["red_bands"] = [_hsv_map(b) for b in red_bands]
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
