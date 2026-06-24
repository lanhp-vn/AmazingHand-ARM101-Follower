"""Pure detection helpers for the system-camera view calibration (device layer).

Re-derives the Aurora screen ROI (deskewed to an upright 5:3 crop), the two mirror-symmetric
alignment-arc bands on the camera circle, and the red HSV band(s) from three sample frames
(white startup screen, red/misaligned arcs, bright/aligned screen). Pure numpy + cv2 imgproc
-- no HighGUI window; the one I/O function is ``write_calibration_values`` (ruamel round-trip).
Synthetic-numpy unit-testable, mirroring ``arc_detector.py``. The interactive capture/confirm
shell lives in ``scripts/calibration/system_camera/calibrate_view.py``.

The ROI is chosen interactively (manual drag + arrow-key deskew) in the calibrate_view shell; this
module only normalises the chosen rect to a 5:3 deskewed RoiBox, fits the camera circle, derives the
symmetric arc bands, samples the red HSV band, and round-trips the config. Plain opencv-python only
(no contrib).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from arm101_hand.config.system_camera_config import (
    AutoTriggerConfig,
    HsvBand,
    RoiBox,
    SystemCameraConfig,
)

from .arc_detector import detect  # reuse the EXACT runtime coverage path so calibration matches runtime
from .roi import roi_from_region

_Rect = tuple[tuple[float, float], tuple[float, float], float]


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

    Splits the bright disc from the dark screen bezel with **Otsu** (the histogram is bimodal: dark
    plastic surround vs bright disc), takes the largest blob, and fits a circle via the contour's
    area (radius = sqrt(area/pi)) about its centroid -- tighter than minEnclosingCircle, which the
    glare streak inflates. A fixed/percentile cut was tried first but FRAGMENTS a non-uniformly-lit
    disc (the real Aurora disc has a bright lobe + a dimmer side), keeping only the bright part and
    mis-centring the fit; Otsu adapts to the disc-vs-bezel split and recovers the whole disc. A
    MORPH_CLOSE bridges the within-disc gradient dips / glare gaps into one solid blob."""
    g = cv2.cvtColor(bright_ref_bgr, cv2.COLOR_BGR2GRAY)
    _, m = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
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
    middle 60% vertically) -- excludes the top corners (battery) and the top/bottom of the disc.

    Mirrored about the CIRCLE centre ``cx`` (so each band sits on the disc's own left/right edge),
    NOT about the frame centre: the deskewed disc is not necessarily centred in the crop, and a
    frame-centre mirror misplaces the right band when it is off-centre (bench bug: a disc at cx=511
    put both bands near the frame centre, missing both arcs)."""
    bw = max(1, int(round(0.35 * r)))
    y = max(0, int(round(cy - 0.6 * r)) - pad)
    h = min(ref_h - y, int(round(1.2 * r)) + 2 * pad)
    lx = int(round(cx - r)) - pad
    rx = int(round(2 * cx - lx - bw))  # mirror the left band about cx
    lx = max(0, min(ref_w - bw, lx))
    rx = max(0, min(ref_w - bw, rx))
    left = RoiBox(x=lx, y=y, w=bw, h=h, ref_w=ref_w, ref_h=ref_h)
    right = RoiBox(x=rx, y=y, w=bw, h=h, ref_w=ref_w, ref_h=ref_h)
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


def describe_case(expected: str, det_left: bool, det_right: bool) -> str:
    """Human sentence comparing the on-screen ground truth (`expected` is 'red' or 'clear' and
    applies to BOTH arcs, which are physically the same colour) against the detector's per-arc
    output (`det_left`/`det_right` True == detector said RED). Collapses to 'both arcs ...' when the
    two arcs share a phrase, else 'left arc ...; right arc ...'."""
    exp_red = expected == "red"

    def phrase(detected_red: bool) -> str:
        if exp_red and detected_red:
            return "correctly detected as red"
        if exp_red and not detected_red:
            return "incorrectly detected as not red"
        if not exp_red and not detected_red:
            return "correctly detected as not red"
        return "incorrectly detected as red"

    lp, rp = phrase(det_left), phrase(det_right)
    return f"both arcs {lp}" if lp == rp else f"left arc {lp}; right arc {rp}"


def pick_threshold(
    red_covs: list[float], clear_covs: list[float], *, floor: float = 0.02
) -> tuple[float, bool]:
    """Pick a coverage threshold separating red-case coverages (should be >= t) from clear-case
    coverages (should be < t).

    Separable (max(clear) < min(red)): max-margin midpoint, separable=True. Overlapping: scan
    candidate cuts (midpoints between adjacent pooled coverages, plus the two outer edges) and return
    the one minimising misclassified coverages, tie-broken by the larger distance to the nearest
    coverage (a 1-D two-class separator -- the Otsu/Triangle idea reimplemented for scalar coverages),
    separable=False. Floored to `floor` so a degenerate all-low set never yields t<=0 (which would
    call everything RED). Empty inputs -> (floor, True)."""
    if not red_covs or not clear_covs:
        return floor, True
    lo_red, hi_clear = min(red_covs), max(clear_covs)
    if hi_clear < lo_red:  # cleanly separable -> midpoint, max margin
        return max((lo_red + hi_clear) / 2.0, floor), True
    pooled = sorted(set(red_covs + clear_covs))
    candidates = [(pooled[i] + pooled[i + 1]) / 2.0 for i in range(len(pooled) - 1)]
    candidates += [max(floor, pooled[0] - 1e-3), pooled[-1] + 1e-3]

    def score(t: float) -> tuple[int, float]:
        mis = sum(c >= t for c in clear_covs) + sum(c < t for c in red_covs)
        margin = min(abs(c - t) for c in pooled)
        return (-mis, margin)

    best_t = max(candidates, key=score)
    return max(best_t, floor), False


@dataclass(frozen=True)
class ArcCase:
    """One labelled deskewed-ROI frame. `expected` ('red'|'clear') applies to BOTH arcs (they are
    physically the same colour); per-arc detector disagreement is a detection error."""

    frame: np.ndarray  # deskewed ROI, ref_w x ref_h, BGR
    expected: str


@dataclass(frozen=True)
class SweepResult:
    """Best-effort tuned detection: the red bands + threshold, whether every case is satisfied, the
    indices still misclassified, and the per-case (det_left, det_right) under the chosen config."""

    red_bands: list[HsvBand]
    coverage_threshold: float
    separable: bool
    unsatisfied: list[int]
    case_detections: list[tuple[bool, bool]]


def sweep_red_detection(
    cases: list[ArcCase],
    left_arc: RoiBox,
    right_arc: RoiBox,
    *,
    morph_kernel: int = 3,
    s_floors: Sequence[int] = tuple(range(20, 130, 10)),
    v_floors: Sequence[int] = tuple(range(30, 150, 10)),
) -> SweepResult:
    """Tune the red HSV band + coverage threshold against all labelled cases.

    1. Hue anchor: ``sample_red_band`` on the arc crops of the first 'red' case -> fixed hue bounds
       (+ the 0/180 wrap -> up to two bands).
    2. Grid over (s_floor, v_floor): build candidate red_bands (anchored hue/s_hi/v_hi, swept floors),
       compute per-arc coverage for every case via the runtime ``detect`` (same masking + morph),
       pool red-case vs clear-case coverages, threshold = ``pick_threshold``.
    3. Score by (#cases with BOTH arcs classified to expected, then separation margin); return the
       best as a SweepResult with red_bands + threshold + the unsatisfied indices + per-case detections.

    Raises ValueError if there is no 'red' case to anchor the hue."""
    red_cases = [c for c in cases if c.expected == "red"]
    if not red_cases:
        raise ValueError("sweep needs at least one 'red' case to anchor the hue")
    anchor_frame = red_cases[0].frame
    try:
        anchor = sample_red_band(roi_from_region(left_arc).crop(anchor_frame))
    except ValueError:
        anchor = sample_red_band(roi_from_region(right_arc).crop(anchor_frame))

    best_key: tuple[int, float] = (-1, -1.0)
    best: tuple[list[HsvBand], float, list[tuple[bool, bool]]] | None = None
    for s_floor in s_floors:
        for v_floor in v_floors:
            bands = [
                HsvBand(h_lo=b.h_lo, s_lo=s_floor, v_lo=v_floor, h_hi=b.h_hi, s_hi=b.s_hi, v_hi=b.v_hi)
                for b in anchor
            ]
            trial = AutoTriggerConfig(
                left_arc=left_arc, right_arc=right_arc, red_bands=bands, morph_kernel=morph_kernel
            )
            covs = []
            for c in cases:
                st = detect(c.frame, trial)
                covs.append((st.left_cov, st.right_cov))
            red_covs = [v for c, lr in zip(cases, covs, strict=True) if c.expected == "red" for v in lr]
            clear_covs = [v for c, lr in zip(cases, covs, strict=True) if c.expected != "red" for v in lr]
            t, _sep = pick_threshold(red_covs, clear_covs)
            dets = [(lc >= t, rc >= t) for (lc, rc) in covs]
            correct = sum(
                dl == (c.expected == "red") and dr == (c.expected == "red")
                for c, (dl, dr) in zip(cases, dets, strict=True)
            )
            allv = red_covs + clear_covs
            margin = min((abs(v - t) for v in allv), default=0.0)
            key = (correct, margin)
            if key > best_key:
                best_key = key
                best = (bands, t, dets)

    assert best is not None  # the grid is non-empty, so a best is always chosen
    bands, t, dets = best
    unsatisfied = [
        i
        for i, (c, (dl, dr)) in enumerate(zip(cases, dets, strict=True))
        if not (dl == (c.expected == "red") and dr == (c.expected == "red"))
    ]
    return SweepResult(
        red_bands=bands,
        coverage_threshold=round(float(t), 4),
        separable=not unsatisfied,
        unsatisfied=unsatisfied,
        case_detections=dets,
    )


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
